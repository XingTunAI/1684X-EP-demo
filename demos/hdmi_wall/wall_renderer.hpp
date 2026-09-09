#ifndef HDMI_WALL_RENDERER_HPP
#define HDMI_WALL_RENDERER_HPP

#include <opencv2/opencv.hpp>
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

// One latest host thumbnail per input. Inference threads call submit(); one
// dedicated thread calls run(). The caller owns and joins that thread before
// destroying this object. A separate system ffplay process reads preview.bgr as
// rawvideo: -f rawvideo -pixel_format bgr24 -video_size 1920x1080 -framerate 10.
class WallRenderer {
public:
    WallRenderer(size_t count, const std::string& output_dir, int device,
                 const std::string& model_name, const std::string& policy = "all")
        : count_(count), output_dir_(output_dir), device_(device),
          model_name_(model_name), policy_(policy), frames_(count), stopped_(false),
          failed_(false), started_(false), rendered_(0), published_(0) {
        if (!count || count > 36) throw std::runtime_error("Wall requires 1 to 36 inputs");
        if (output_dir.empty()) throw std::runtime_error("Wall output directory is empty");
        struct stat info;
        if (::stat(output_dir.c_str(), &info) || !S_ISDIR(info.st_mode))
            throw std::runtime_error("Wall output directory must already exist");
        const std::string path = fifo_path();
        if (::mkfifo(path.c_str(), 0600) && errno != EEXIST)
            throw std::runtime_error("Cannot create preview FIFO: " + std::string(std::strerror(errno)));
        // Do not follow a symlink or accidentally overwrite a regular file.
        if (::lstat(path.c_str(), &info) || !S_ISFIFO(info.st_mode))
            throw std::runtime_error("preview.bgr exists and is not a FIFO");
    }

    ~WallRenderer() { stop(); }
    WallRenderer(const WallRenderer&) = delete;
    WallRenderer& operator=(const WallRenderer&) = delete;

    std::string fifo_path() const { return output_dir_ + "/preview.bgr"; }
    bool failed() const { return failed_.load(); }
    std::string error() const {
        std::lock_guard<std::mutex> guard(error_lock_);
        return error_;
    }
    void stop() {
        stopped_.store(true);
        wake_.notify_all();
    }

    void submit(size_t id, const cv::Mat& host_bgr, uint64_t frame_count,
                size_t detections, double process_fps, double frame_age_ms = 0,
                double source_age_ms = -1, uint64_t policy_drops = 0) {
        if (stopped_.load()) return;
        const auto submit_started = Clock::now();
        if (id >= count_) throw std::runtime_error("Invalid wall input index");
        if (host_bgr.empty() || host_bgr.type() != CV_8UC3)
            throw std::runtime_error("Wall submit requires nonempty host CV_8UC3 BGR");
        Frame frame;
        const double scale = std::min(1.0, std::min(316.0 / host_bgr.cols, 164.0 / host_bgr.rows));
        if (scale < 1.0) {
            cv::resize(host_bgr, frame.image,
                       cv::Size(std::max(1, static_cast<int>(host_bgr.cols * scale)),
                                std::max(1, static_cast<int>(host_bgr.rows * scale))),
                       0, 0, cv::INTER_AREA);
        } else {
            frame.image = host_bgr.clone();
        }
        frame.count = frame_count;
        frame.detections = detections;
        frame.fps = std::isfinite(process_fps) && process_fps >= 0 ? process_fps : 0;
        frame.updated = Clock::now();
        // Ages arrive at submit entry; include thumbnail work before recording
        // the update time. Source age is available only for paced local video.
        const double thumbnail_ms = std::chrono::duration<double, std::milli>(
            frame.updated - submit_started).count();
        frame.frame_age_ms = (std::isfinite(frame_age_ms) && frame_age_ms >= 0 ? frame_age_ms : 0) + thumbnail_ms;
        frame.source_age_ms = std::isfinite(source_age_ms) && source_age_ms >= 0
            ? source_age_ms + thumbnail_ms : -1;
        frame.policy_drops = policy_drops;
        frame.unix_ms = unix_millis();
        std::lock_guard<std::mutex> guard(frames_lock_);
        frames_[id] = frame;
    }

    void run() {
        if (started_.exchange(true)) {
            fail("WallRenderer::run may only be called once");
            return;
        }
        int descriptor = -1;
        try {
            // Block SIGPIPE only in the rendering thread. This lets EPIPE turn
            // into a reported player disconnect without terminating inference.
            PipeSignalMask signal_mask;
            const auto open_deadline = Clock::now() + std::chrono::seconds(20);
            while (!stopped_.load()) {
                descriptor = ::open(fifo_path().c_str(), O_WRONLY | O_NONBLOCK | O_CLOEXEC);
                if (descriptor >= 0) break;
                if (errno != ENXIO && errno != EINTR)
                    throw std::runtime_error("Cannot open preview FIFO: " + std::string(std::strerror(errno)));
                if (Clock::now() >= open_deadline)
                    throw std::runtime_error("No preview FIFO reader within 20 seconds");
                wait_until(Clock::now() + std::chrono::milliseconds(100));
            }
            auto next_frame = Clock::now();
            auto next_snapshot = next_frame;
            while (!stopped_.load()) {
                const auto now = Clock::now();
                const auto frames = snapshot_frames();
                cv::Mat canvas = render(frames, now);
                ++rendered_;
                if (now >= next_snapshot) {
                    snapshot_files(canvas, frames, now);
                    next_snapshot = now + std::chrono::seconds(5);
                }
                if (!write_frame(descriptor, canvas)) break;
                ++published_;
                next_frame = std::max(next_frame + std::chrono::milliseconds(100), Clock::now());
                wait_until(next_frame);
            }
        } catch (const std::exception& exception) {
            fail(exception.what());
        } catch (...) {
            fail("Unknown wall renderer failure");
        }
        if (descriptor >= 0) ::close(descriptor);
        // Preserve a final image/status even if the player disconnects.
        try {
            const auto now = Clock::now();
            const auto frames = snapshot_frames();
            snapshot_files(render(frames, now), frames, now);
        } catch (const std::exception& exception) {
            fail(exception.what());
        }
    }

private:
    typedef std::chrono::steady_clock Clock;
    typedef Clock::time_point Time;
    struct Frame {
        cv::Mat image;
        uint64_t count = 0;
        size_t detections = 0;
        double fps = 0;
        double frame_age_ms = 0;
        double source_age_ms = -1;
        uint64_t policy_drops = 0;
        Time updated;
        int64_t unix_ms = 0;
    };
    struct PipeSignalMask {
        sigset_t blocked, previous;
        bool restore;
        PipeSignalMask() : restore(false) {
            ::sigemptyset(&blocked);
            ::sigaddset(&blocked, SIGPIPE);
            const int result = ::pthread_sigmask(SIG_BLOCK, &blocked, &previous);
            if (result) throw std::runtime_error("Cannot block SIGPIPE: " + std::string(std::strerror(result)));
            restore = true;
        }
        ~PipeSignalMask() {
            if (!restore) return;
            if (!::sigismember(&previous, SIGPIPE)) {
                const timespec zero = {0, 0};
                for (;;) {
                    if (::sigtimedwait(&blocked, NULL, &zero) >= 0) continue;
                    if (errno == EINTR) continue;
                    break;
                }
            }
            ::pthread_sigmask(SIG_SETMASK, &previous, NULL);
        }
    };
    size_t count_;
    std::string output_dir_;
    int device_;
    std::string model_name_;
    std::string policy_;
    std::vector<Frame> frames_;
    mutable std::mutex frames_lock_, error_lock_, wait_lock_;
    std::condition_variable wake_;
    std::atomic<bool> stopped_, failed_, started_;
    std::string error_;
    uint64_t rendered_, published_;

    static int64_t unix_millis() {
        return std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    }
    static double age_seconds(const Frame& frame, Time now) {
        return frame.image.empty() ? -1 : std::max(0.0, std::chrono::duration<double>(now - frame.updated).count());
    }
    static double frame_age_millis(const Frame& frame, Time now) {
        const double elapsed = age_seconds(frame, now);
        return elapsed < 0 ? -1 : frame.frame_age_ms + elapsed * 1000;
    }
    static double source_age_millis(const Frame& frame, Time now) {
        const double elapsed = age_seconds(frame, now);
        return elapsed < 0 || frame.source_age_ms < 0 ? -1 : frame.source_age_ms + elapsed * 1000;
    }
    static double freshness_age_millis(const Frame& frame, Time now) {
        const double source_age = source_age_millis(frame, now);
        return source_age >= 0 ? source_age : frame_age_millis(frame, now);
    }
    void fail(const std::string& message) {
        {
            std::lock_guard<std::mutex> guard(error_lock_);
            if (error_.empty()) error_ = message;
        }
        failed_.store(true);
        stop();
    }
    void wait_until(Time when) {
        std::unique_lock<std::mutex> guard(wait_lock_);
        wake_.wait_until(guard, when, [this] { return stopped_.load(); });
    }
    std::vector<Frame> snapshot_frames() const {
        std::lock_guard<std::mutex> guard(frames_lock_);
        return frames_; // cv::Mat refcounts keep these immutable thumbnails alive.
    }
    static void label(cv::Mat& canvas, const std::string& text, int x, int y,
                      double scale, const cv::Scalar& color, int weight = 1) {
        cv::putText(canvas, text, cv::Point(x, y), cv::FONT_HERSHEY_SIMPLEX,
                    scale, color, weight, cv::LINE_AA);
    }
    cv::Mat render(const std::vector<Frame>& frames, Time now) const {
        cv::Mat canvas(1080, 1920, CV_8UC3, cv::Scalar(18, 15, 12));
        const cv::Scalar white(245, 243, 239), gray(180, 176, 170), cyan(208, 208, 58), red(65, 65, 245);
        std::ostringstream title;
        title << count_ << "CH  /  " << model_name_ << "  /  Device " << device_ << "  /  Policy " << policy_;
        label(canvas, title.str(), 20, 32, .8, white, 2);
        label(canvas, "Live bounding boxes | HDMI preview 10 FPS | Age: local source / decode; excludes camera and display latency", 20, 58, .48, gray);
        for (size_t id = 0; id < 36; ++id) {
            const int x = static_cast<int>(id % 6) * 320;
            const int y = 72 + static_cast<int>(id / 6) * 168;
            const cv::Rect tile(x + 1, y + 1, 318, 166);
            cv::rectangle(canvas, tile, cv::Scalar(40, 38, 34), -1);
            if (id >= frames.size()) {
                const size_t note = (id - frames.size()) % 4;
                const std::string channel_title = std::to_string(count_) + " INDEPENDENT CHANNELS";
                const char* headings[] = {channel_title.c_str(), "LIVE DETECTIONS", "DISPLAY RATE", "SOURCE AND STATUS"};
                const char* lines[] = {"Independent local decoders", "Boxes from each channel", "Preview capped 10 FPS", "Repeated local test video"};
                const char* sublines[] = {"One decoder per channel", "Live detections", "Inference FPS shown per tile", "Red STALE if older than 2s"};
                label(canvas, headings[note], x + 12, y + 46, .49, cyan, 1);
                label(canvas, lines[note], x + 12, y + 83, .50, white);
                label(canvas, sublines[note], x + 12, y + 115, .43, gray);
                continue;
            }
            const Frame& frame = frames[id];
            if (!frame.image.empty()) {
                const double scale = std::min(316.0 / frame.image.cols, 164.0 / frame.image.rows);
                const int width = std::max(1, std::min(316, static_cast<int>(frame.image.cols * scale)));
                const int height = std::max(1, std::min(164, static_cast<int>(frame.image.rows * scale)));
                cv::Mat scaled;
                cv::resize(frame.image, scaled, cv::Size(width, height), 0, 0, cv::INTER_LINEAR);
                scaled.copyTo(canvas(cv::Rect(x + (320 - width) / 2, y + (168 - height) / 2, width, height)));
            } else {
                label(canvas, "WAITING FOR INFERENCE", x + 32, y + 91, .48, gray);
            }
            cv::rectangle(canvas, cv::Rect(x + 2, y + 2, 316, 23), cv::Scalar(16, 15, 12), -1);
            cv::rectangle(canvas, cv::Rect(x + 2, y + 131, 316, 35), cv::Scalar(16, 15, 12), -1);
            std::ostringstream top, bottom, timing;
            top << "CH " << std::setw(2) << std::setfill('0') << id + 1 << "   infer " << std::fixed << std::setprecision(1) << frame.fps << " FPS";
            bottom << "frame " << frame.count << "    det " << frame.detections;
            const double age_ms = freshness_age_millis(frame, now);
            timing << (frame.source_age_ms >= 0 ? "src age " : "dec age ");
            if (age_ms < 0) timing << "--";
            else timing << std::fixed << std::setprecision(0) << age_ms << "ms";
            timing << "  drop " << frame.policy_drops;
            label(canvas, top.str(), x + 9, y + 18, .45, white);
            label(canvas, bottom.str(), x + 9, y + 144, .38, gray);
            label(canvas, timing.str(), x + 9, y + 160, .38, gray);
            if (age_ms > 2000) {
                cv::rectangle(canvas, cv::Rect(x + 237, y + 29, 78, 23), cv::Scalar(16, 15, 12), -1);
                label(canvas, "STALE", x + 245, y + 46, .46, red, 2);
                cv::rectangle(canvas, tile, red, 2);
            }
        }
        return canvas;
    }
    bool write_frame(int descriptor, const cv::Mat& canvas) {
        if (descriptor < 0 || !canvas.isContinuous()) throw std::runtime_error("Invalid rawvideo frame");
        size_t offset = 0;
        const size_t bytes = canvas.total() * canvas.elemSize();
        while (offset < bytes && !stopped_.load()) {
            const ssize_t count = ::write(descriptor, canvas.data + offset, bytes - offset);
            if (count > 0) { offset += static_cast<size_t>(count); continue; }
            if (count < 0 && errno == EINTR) continue;
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                pollfd output = {descriptor, POLLOUT, 0};
                const int result = ::poll(&output, 1, 100);
                if (result < 0 && errno == EINTR) continue;
                if (result < 0) throw std::runtime_error("Preview FIFO poll failed");
                if (result > 0 && (output.revents & (POLLERR | POLLHUP | POLLNVAL)))
                    throw std::runtime_error("HDMI player disconnected from preview FIFO");
                continue;
            }
            throw std::runtime_error(count < 0 && errno == EPIPE
                ? "HDMI player disconnected from preview FIFO"
                : "Preview FIFO write failed: " + std::string(std::strerror(errno)));
        }
        // Never drop a partially written frame and then start another one.
        return offset == bytes;
    }
    static std::string quote(const std::string& value) {
        std::ostringstream out;
        out << '"';
        for (unsigned char character : value) {
            if (character == '"' || character == '\\') out << '\\' << character;
            else if (character < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(character) << std::dec;
            else out << character;
        }
        out << '"';
        return out.str();
    }
    static void little(std::ostream& file, uint32_t value, int bytes) {
        for (int index = 0; index < bytes; ++index) file.put(static_cast<char>((value >> (8 * index)) & 255));
    }
    static void replace_file(const std::string& temporary, const std::string& final_path) {
        if (::rename(temporary.c_str(), final_path.c_str()))
            throw std::runtime_error("Cannot publish wall snapshot: " + std::string(std::strerror(errno)));
    }
    void snapshot_files(const cv::Mat& canvas, const std::vector<Frame>& frames, Time now) {
        const std::string bitmap = output_dir_ + "/wall.bmp";
        {
            std::ofstream file(bitmap + ".tmp", std::ios::binary | std::ios::trunc);
            file.exceptions(std::ios::failbit | std::ios::badbit);
            const uint32_t stride = (canvas.cols * 3 + 3) & ~3;
            const uint32_t size = stride * canvas.rows;
            file.put('B'); file.put('M'); little(file, 54 + size, 4); little(file, 0, 4); little(file, 54, 4);
            little(file, 40, 4); little(file, canvas.cols, 4); little(file, canvas.rows, 4);
            little(file, 1, 2); little(file, 24, 2); little(file, 0, 4); little(file, size, 4);
            little(file, 2835, 4); little(file, 2835, 4); little(file, 0, 4); little(file, 0, 4);
            for (int row = canvas.rows - 1; row >= 0; --row) {
                file.write(reinterpret_cast<const char*>(canvas.ptr(row)), canvas.cols * 3);
                for (uint32_t padding = canvas.cols * 3; padding < stride; ++padding) file.put(0);
            }
            file.close();
        }
        replace_file(bitmap + ".tmp", bitmap);
        const std::string status = output_dir_ + "/status.json";
        {
            std::ofstream file(status + ".tmp", std::ios::trunc);
            file.exceptions(std::ios::failbit | std::ios::badbit);
            file << "{\n  \"timestamp_unix_ms\": " << unix_millis()
                 << ",\n  \"width\": 1920, \"height\": 1080, \"columns\": 6, \"rows\": 6,"
                 << "\n  \"preview_fps_cap\": 10, \"channels\": " << count_
                 << ", \"device\": " << device_ << ", \"model\": " << quote(model_name_)
                 << ", \"policy\": " << quote(policy_)
                 << ",\n  \"age_note\": \"frame_age_ms starts at decode completion; source_age_ms starts at local frame due time and is null for RTSP. Both include time since submission; neither is camera-to-display latency.\""
                 << ",\n  \"rendered_frames\": " << rendered_ << ", \"published_frames\": " << published_
                 << ", \"stopped\": " << (stopped_.load() ? "true" : "false")
                 << ", \"failed\": " << (failed_.load() ? "true" : "false")
                 << ", \"error\": " << quote(error()) << ",\n  \"streams\": [\n";
            for (size_t id = 0; id < frames.size(); ++id) {
                const Frame& frame = frames[id];
                const double age = age_seconds(frame, now);
                const double frame_age = frame_age_millis(frame, now);
                const double source_age = source_age_millis(frame, now);
                if (id) file << ",\n";
                file << "    {\"id\": " << id << ", \"label\": " << id + 1
                     << ", \"frame\": " << frame.count << ", \"detections\": " << frame.detections
                     << ", \"inference_fps\": " << std::setprecision(8) << frame.fps
                     << ", \"updated_unix_ms\": " << frame.unix_ms << ", \"age_seconds\": ";
                if (age < 0) file << "null"; else file << age;
                file << ", \"frame_age_ms\": ";
                if (frame_age < 0) file << "null"; else file << frame_age;
                file << ", \"source_age_ms\": ";
                if (source_age < 0) file << "null"; else file << source_age;
                file << ", \"policy_drops\": " << frame.policy_drops
                     << ", \"stale\": " << (freshness_age_millis(frame, now) > 2000 ? "true" : "false")
                     << ", \"has_image\": " << (frame.image.empty() ? "false" : "true") << '}';
            }
            file << "\n  ]\n}\n";
            file.close();
        }
        replace_file(status + ".tmp", status);
    }
};

#endif
