#ifndef HDMI_WALL_RENDERER_HPP
#define HDMI_WALL_RENDERER_HPP

#include "decode_observation.hpp"
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
    typedef std::chrono::steady_clock Clock;
    typedef Clock::time_point Time;
    WallRenderer(size_t count, const std::string& output_dir, int device,
                 const std::string& model_name, const std::string& policy = "all")
        : count_(count), output_dir_(output_dir), device_(device),
          model_name_(model_name), policy_(policy), frames_(count), raw_frames_(count), observation_(count),
          scaled_tiles_(count), observation_raw_tiles_(count), observation_detected_tiles_(count), stopped_(false),
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

    void configure_observation(bool enabled, bool inference_enabled, const std::vector<size_t>& selected) {
        if (started_.load()) throw std::logic_error("Configure observation before starting renderer");
        std::lock_guard<std::mutex> guard(frames_lock_);
        std::vector<size_t> choices = selected;
        if (enabled && choices.empty()) { choices.push_back(0); if (count_ > 1) choices.push_back(1); }
        if (enabled && (choices.empty() || choices.size() > 4)) throw std::invalid_argument("Select one to four observation channels");
        std::vector<bool> seen(count_, false);
        for (size_t id : choices) {
            if (id >= count_ || seen[id]) throw std::invalid_argument("Invalid or duplicate observation channel");
            seen[id] = true;
        }
        observation_enabled_ = enabled; observation_inference_ = inference_enabled;
        observation_selected_ = choices; selected_mask_ = seen;
    }
    void configure_source(size_t id, double source_fps, bool live) {
        if (!observation_enabled_) return;
        std::lock_guard<std::mutex> guard(frames_lock_);
        observation_.configure_source(id, source_fps, live);
    }
    void observation_start(Time start) {
        if (!observation_enabled_) return;
        std::lock_guard<std::mutex> guard(frames_lock_);
        observation_.start(monotonic_seconds(start));
    }
    void observe_decode(size_t id, uint64_t sequence, double source_seconds, Time after_decode,
                        double source_lag_ms, double read_ms) {
        if (!observation_enabled_ || stopped_.load()) return;
        std::lock_guard<std::mutex> guard(frames_lock_);
        observation_.decoded(id, sequence, source_seconds, monotonic_seconds(after_decode), source_lag_ms, read_ms);
    }
    void observe_inference(size_t id, uint64_t sequence, double source_seconds, Time completed) {
        if (!observation_enabled_ || stopped_.load()) return;
        std::lock_guard<std::mutex> guard(frames_lock_);
        observation_.inferred(id, sequence, source_seconds, monotonic_seconds(completed));
    }
    void submit_decoded(size_t id, const cv::Mat& host_bgr, uint64_t sequence, double source_seconds,
                        Time decoded_at, double source_lag_ms) {
        if (!observation_enabled_ || stopped_.load()) return;
        if (id >= count_) throw std::invalid_argument("Invalid decoded preview channel");
        if (!selected_mask_[id]) return;
        if (host_bgr.empty() || host_bgr.type() != CV_8UC3)
            throw std::invalid_argument("Decoded preview requires host CV_8UC3 BGR");
        Frame frame;
        copy_thumbnail(host_bgr, frame.image, 640, 360);
        frame.count = sequence + 1; frame.sequence = sequence;
        frame.source_seconds = valid_nonnegative(source_seconds);
        frame.updated = Clock::now(); frame.decoded_at = decoded_at; frame.has_decode_time = true;
        frame.frame_age_ms = std::max(0.0, std::chrono::duration<double, std::milli>(frame.updated - decoded_at).count());
        frame.source_age_ms = valid_nonnegative(source_lag_ms);
        if (frame.source_age_ms >= 0) frame.source_age_ms += frame.frame_age_ms;
        frame.unix_ms = unix_millis();
        std::lock_guard<std::mutex> guard(frames_lock_);
        // An asynchronous thumbnail must never overwrite a newer decoded view.
        if (raw_frames_[id].image.empty() || sequence > raw_frames_[id].sequence) raw_frames_[id] = frame;
    }

    void submit(size_t id, const cv::Mat& host_bgr, uint64_t frame_count,
                size_t detections, double process_fps, double frame_age_ms = 0,
                double source_age_ms = -1, uint64_t policy_drops = 0, double source_seconds = -1) {
        if (stopped_.load()) return;
        const auto submit_started = Clock::now();
        if (id >= count_) throw std::runtime_error("Invalid wall input index");
        if (host_bgr.empty() || host_bgr.type() != CV_8UC3)
            throw std::runtime_error("Wall submit requires nonempty host CV_8UC3 BGR");
        Frame frame;
        const bool large = observation_enabled_ && selected_mask_[id];
        copy_thumbnail(host_bgr, frame.image, large ? 640 : 316, large ? 360 : 164);
        frame.count = frame_count;
        frame.sequence = frame_count ? frame_count - 1 : 0;
        frame.source_seconds = valid_nonnegative(source_seconds);
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
                const auto view = snapshot_view(now);
                cv::Mat canvas = render(view.frames, now, view);
                ++rendered_;
                if (now >= next_snapshot) {
                    snapshot_files(canvas, view.frames, now, view);
                    next_snapshot = now + std::chrono::seconds(observation_enabled_ ? 1 : 5);
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
            const auto view = snapshot_view(now);
            snapshot_files(render(view.frames, now, view), view.frames, now, view);
        } catch (const std::exception& exception) {
            fail(exception.what());
        }
    }

private:
    struct Frame {
        cv::Mat image;
        uint64_t count = 0;
        uint64_t sequence = 0;
        double source_seconds = -1;
        size_t detections = 0;
        double fps = 0;
        double frame_age_ms = 0;
        double source_age_ms = -1;
        uint64_t policy_drops = 0;
        Time updated, decoded_at;
        bool has_decode_time = false;
        int64_t unix_ms = 0;
    };
    struct ScaledTile {
        cv::Mat image;
        uint64_t source_count = 0;
        Time source_updated;
    };
    struct ViewSnapshot {
        std::vector<Frame> frames, raw_frames;
        std::vector<decode_observation::StreamSnapshot> metrics;
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
    std::vector<Frame> raw_frames_;
    decode_observation::Ledger observation_;
    bool observation_enabled_ = false, observation_inference_ = true;
    std::vector<size_t> observation_selected_;
    std::vector<bool> selected_mask_;
    // Only run()/render() access these owned pixels. submit() continues to
    // publish immutable thumbnails under frames_lock_; no shared cache writes.
    std::vector<ScaledTile> scaled_tiles_;
    std::vector<ScaledTile> observation_raw_tiles_, observation_detected_tiles_;
    mutable std::mutex frames_lock_, error_lock_, wait_lock_;
    std::condition_variable wake_;
    std::atomic<bool> stopped_, failed_, started_;
    std::string error_;
    uint64_t rendered_, published_;

    static double monotonic_seconds(Time time) { return std::chrono::duration<double>(time.time_since_epoch()).count(); }
    static double valid_nonnegative(double value) { return std::isfinite(value) && value >= 0 ? value : -1; }
    static void copy_thumbnail(const cv::Mat& input, cv::Mat& output, double width, double height) {
        const double scale = std::min(1.0, std::min(width / input.cols, height / input.rows));
        if (scale < 1.0) cv::resize(input, output, cv::Size(std::max(1, static_cast<int>(input.cols * scale)),
                                                        std::max(1, static_cast<int>(input.rows * scale))), 0, 0, cv::INTER_AREA);
        else output = input.clone();
    }

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
    ViewSnapshot snapshot_view(Time now) {
        std::lock_guard<std::mutex> guard(frames_lock_);
        ViewSnapshot view;
        view.frames = frames_; // cv::Mat refcounts retain immutable thumbnails, no pixel copies.
        if (observation_enabled_) { view.raw_frames = raw_frames_; view.metrics = observation_.snapshot(monotonic_seconds(now)); }
        return view;
    }
    static void label(cv::Mat& canvas, const std::string& text, int x, int y,
                      double scale, const cv::Scalar& color, int weight = 1) {
        cv::putText(canvas, text, cv::Point(x, y), cv::FONT_HERSHEY_SIMPLEX,
                    scale, color, weight, cv::LINE_AA);
    }
    cv::Mat render(const std::vector<Frame>& frames, Time now, const ViewSnapshot& view) {
        if (observation_enabled_) return render_observation(view, now);
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
            ScaledTile& scaled = scaled_tiles_[id];
            if (!frame.image.empty()) {
                // A wall tick often repeats the same submitted inference frame.
                // Reuse only its resized pixels; all ages and status below are
                // recomputed every tick. updated also invalidates a resubmitted
                // source count, while the image and its boxes stay together.
                if (scaled.image.empty() || scaled.source_count != frame.count ||
                    scaled.source_updated != frame.updated) {
                    const double scale = std::min(316.0 / frame.image.cols, 164.0 / frame.image.rows);
                    const int width = std::max(1, std::min(316, static_cast<int>(frame.image.cols * scale)));
                    const int height = std::max(1, std::min(164, static_cast<int>(frame.image.rows * scale)));
                    cv::resize(frame.image, scaled.image, cv::Size(width, height), 0, 0, cv::INTER_LINEAR);
                    scaled.source_count = frame.count;
                    scaled.source_updated = frame.updated;
                }
                const int width = scaled.image.cols, height = scaled.image.rows;
                scaled.image.copyTo(canvas(cv::Rect(x + (320 - width) / 2, y + (168 - height) / 2, width, height)));
            } else {
                scaled.image.release();
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
    static std::string number(double value, int digits = 1) {
        if (!std::isfinite(value) || value < 0) return "--";
        std::ostringstream out; out << std::fixed << std::setprecision(digits) << value; return out.str();
    }
    static std::string observation_state(const decode_observation::StreamSnapshot& row) {
        if (row.warming) return "START";
        if (row.stalled) return "STALL";
        if (row.sustained_slow) return "SLOW";
        return row.target_fps > 0 ? "OK" : "NO TARGET";
    }
    void observation_image(cv::Mat& canvas, const Frame& frame, ScaledTile& cached, const cv::Rect& box,
                           Time now, const std::string& empty_text) {
        cv::rectangle(canvas, box, cv::Scalar(27, 25, 22), -1);
        if (frame.image.empty()) {
            label(canvas, empty_text, box.x + 18, box.y + box.height / 2, .55, cv::Scalar(180, 176, 170));
            return;
        }
        if (cached.image.empty() || cached.source_count != frame.count || cached.source_updated != frame.updated) {
            const double scale = std::min(static_cast<double>(box.width) / frame.image.cols,
                                          static_cast<double>(box.height) / frame.image.rows);
            cv::resize(frame.image, cached.image,
                       cv::Size(std::max(1, std::min(box.width, static_cast<int>(frame.image.cols * scale))),
                                std::max(1, std::min(box.height, static_cast<int>(frame.image.rows * scale)))),
                       0, 0, cv::INTER_LINEAR);
            cached.source_count = frame.count; cached.source_updated = frame.updated;
        }
        cached.image.copyTo(canvas(cv::Rect(box.x + (box.width - cached.image.cols) / 2,
                                            box.y + (box.height - cached.image.rows) / 2,
                                            cached.image.cols, cached.image.rows)));
        if (freshness_age_millis(frame, now) > 2000) {
            cv::rectangle(canvas, box, cv::Scalar(65, 65, 245), 2);
            cv::rectangle(canvas, cv::Rect(box.x + 4, box.y + 4, 164, 25), cv::Scalar(16, 15, 12), -1);
            label(canvas, "STALE PREVIEW", box.x + 10, box.y + 22, .5, cv::Scalar(65, 65, 245), 2);
        }
    }
    cv::Mat render_observation(const ViewSnapshot& view, Time now) {
        cv::Mat canvas(1080, 1920, CV_8UC3, cv::Scalar(18, 15, 12));
        const cv::Scalar white(245, 243, 239), gray(180, 176, 170), cyan(208, 208, 58),
                         amber(90, 185, 250), red(65, 65, 245);
        double decoded = 0, inferred = 0, target = 0;
        bool known_target = true;
        for (const auto& row : view.metrics) {
            decoded += row.decode_fps; inferred += row.infer_fps;
            if (row.target_fps > 0) target += row.target_fps; else known_target = false;
        }
        label(canvas, std::to_string(count_) + "CH DECODE OBSERVATION / Device " + std::to_string(device_) +
              (observation_inference_ ? " / INFERENCE ON" : " / INFERENCE OFF"), 18, 32, .8, white, 2);
        label(canvas, "TOTAL decode " + number(decoded) + " FPS / target " + (known_target ? number(target) : "N/A") +
              "  |  infer " + (observation_inference_ ? number(inferred) + " FPS" : "OFF") +
              "  |  Rolling 2s, all successful events", 18, 61, .6, cyan);
        label(canvas, "RAW and DETECTION show independent latest frames. HDMI refresh cap: 10 FPS; image sampling may be lower.",
              18, 87, .49, gray);
        const int row_height = 946 / static_cast<int>(observation_selected_.size());
        for (size_t index = 0; index < observation_selected_.size(); ++index) {
            const size_t id = observation_selected_[index];
            const int top = 103 + static_cast<int>(index) * row_height;
            const Frame* frames[] = {&view.raw_frames[id], &view.frames[id]};
            for (int side = 0; side < 2; ++side) {
                const int left = side ? 660 : 18;
                const Frame& frame = *frames[side];
                label(canvas, "CH " + std::to_string(id + 1) + (side ? "  DETECTION / SAME-FRAME BOXES" : "  RAW DECODE / NO BOXES"),
                      left, top + 20, .55, side ? white : cyan, 1);
                const cv::Rect image_box(left, top + 31, 624, row_height - 75);
                observation_image(canvas, frame, side ? observation_detected_tiles_[id] : observation_raw_tiles_[id],
                                  image_box, now, side ? (observation_inference_ ? "WAITING FOR DETECTION" : "INFERENCE DISABLED")
                                                      : "WAITING FOR DECODE PREVIEW");
                const std::string identity = frame.image.empty() ? "--" : std::to_string(frame.sequence);
                label(canvas, "ID " + identity + " | media " + number(frame.source_seconds, 3) + "s | " +
                      (frame.source_age_ms >= 0 ? "src age " : "dec age ") + number(freshness_age_millis(frame, now), 0) + "ms",
                      left + 3, top + row_height - 24, .44, gray);
                label(canvas, side ? "Image and boxes belong to the ID above" : "Preview may skip frames; decode counters include every frame",
                      left + 3, top + row_height - 6, .36, gray);
            }
        }
        cv::rectangle(canvas, cv::Rect(1305, 100, 602, 951), cv::Scalar(30, 27, 24), -1);
        label(canvas, "ALL CHANNELS / FPS / milliseconds", 1320, 123, .52, white);
        const int x[] = {1320, 1364, 1437, 1506, 1578, 1662, 1753};
        const char* headers[] = {"CH", "TARGET", "DEC", "INFER", "LAG", "AGE", "STATE"};
        for (size_t column = 0; column < 7; ++column) label(canvas, headers[column], x[column], 150, .44, gray);
        const int pitch = std::min(26, 870 / static_cast<int>(count_));
        for (size_t id = 0; id < view.metrics.size(); ++id) {
            const auto& row = view.metrics[id];
            const int y = 175 + static_cast<int>(id) * pitch;
            const cv::Scalar color = row.stalled ? red : row.sustained_slow ? amber : white;
            const std::string fields[] = {std::to_string(id + 1), row.target_fps > 0 ? number(row.target_fps) : "N/A",
                number(row.decode_fps), observation_inference_ ? number(row.infer_fps) : "OFF",
                number(row.source_lag_ms, 0), number(row.last_decoded_age_ms, 0), observation_state(row)};
            for (size_t column = 0; column < 7; ++column) {
                const int available = column + 1 < 7 ? x[column + 1] - x[column] - 8 : 1900 - x[column];
                const auto text_size = cv::getTextSize(fields[column], cv::FONT_HERSHEY_SIMPLEX, .44, 1, nullptr);
                const double scale = text_size.width > available ? .44 * available / text_size.width : .44;
                label(canvas, fields[column], x[column], y, scale, color);
            }
        }
        label(canvas, "LAG: last local decode vs schedule. AGE: since last decode. SLOW: decode below 85% target for 3s after 2s startup window.",
              18, 1071, .46, gray);
        return canvas;
    }
    static void json_number(std::ostream& file, double value) {
        if (!std::isfinite(value) || value < 0) file << "null"; else file << value;
    }
    static void write_observation_image(std::ostream& file, const Frame& frame, Time now) {
        file << "{\"has_image\":" << (frame.image.empty() ? "false" : "true") << ",\"sequence\":";
        if (frame.image.empty()) file << "null"; else file << frame.sequence;
        file << ",\"source_seconds\":"; json_number(file, frame.source_seconds);
        file << ",\"updated_monotonic_s\":"; json_number(file, frame.image.empty() ? -1 : monotonic_seconds(frame.updated));
        // submit_decoded receives the actual decode timestamp. The existing
        // detected-image API supplies ages only: never invent an exact time.
        file << ",\"decoded_monotonic_s\":"; json_number(file, frame.image.empty() || !frame.has_decode_time ? -1 : monotonic_seconds(frame.decoded_at));
        file << ",\"view_age_ms\":"; json_number(file, frame.image.empty() ? -1 : age_seconds(frame, now) * 1000);
        file << ",\"frame_age_ms\":"; json_number(file, frame_age_millis(frame, now));
        file << ",\"source_age_ms\":"; json_number(file, source_age_millis(frame, now));
        file << ",\"decode_source_lag_ms\":";
        json_number(file, frame.image.empty() || frame.source_age_ms < 0 ? -1 : std::max(0.0, frame.source_age_ms - frame.frame_age_ms));
        file << ",\"stale\":" << (freshness_age_millis(frame, now) > 2000 ? "true" : "false") << '}';
    }
    void write_observation(std::ostream& file, const ViewSnapshot& view, Time now) const {
        double total_decode = 0, total_infer = 0, target = 0;
        uint64_t decoded_count = 0, inferred_count = 0;
        bool target_known = true;
        for (const auto& row : view.metrics) {
            total_decode += row.decode_fps; total_infer += row.infer_fps;
            decoded_count += row.decoded; inferred_count += row.inferred;
            if (row.target_fps > 0) target += row.target_fps; else target_known = false;
        }
        file << std::setprecision(15) << ",\n  \"observation\": {\"mode\":\"decode_observation\",\"layout\":\"selected_pairs_and_all_channel_metrics\",\"enabled\":true,\"inference_enabled\":"
             << (observation_inference_ ? "true" : "false")
             << ",\"rolling_window_seconds\":2,\"rolling_bucket_seconds\":0.1,\"slow_target_fraction\":0.85,\"slow_duration_seconds\":3"
             << ",\"counter_population\":\"all_successful_decode_before_policy_filter_and_all_detect_completions\""
             << ",\"sequence_base\":0,\"frame_matched_comparison\":false,\"detections_match_their_own_image\":true"
             << ",\"total_decode_fps\":" << total_decode << ",\"total_infer_fps\":" << total_infer
             << ",\"decoded_count\":" << decoded_count << ",\"inferred_count\":" << inferred_count << ",\"target_fps\":";
        json_number(file, target_known ? target : -1);
        file << ",\"selected\":[";
        for (size_t index = 0; index < observation_selected_.size(); ++index) { if (index) file << ','; file << observation_selected_[index]; }
        file << "],\"streams\":[";
        for (size_t id = 0; id < view.metrics.size(); ++id) {
            const auto& row = view.metrics[id];
            if (id) file << ',';
            file << "{\"id\":" << id << ",\"label\":" << id + 1 << ",\"live\":" << (row.live ? "true" : "false")
                 << ",\"decoded_count\":" << row.decoded << ",\"inferred_count\":" << row.inferred << ",\"target_fps\":";
            json_number(file, row.target_fps);
            file << ",\"decode_fps\":" << row.decode_fps << ",\"infer_fps\":" << row.infer_fps << ",\"decode_sequence\":";
            if (row.has_decode) file << row.decode_sequence; else file << "null";
            file << ",\"infer_sequence\":"; if (row.has_inference) file << row.infer_sequence; else file << "null";
            file << ",\"decode_source_seconds\":"; json_number(file, row.decode_source_seconds);
            file << ",\"infer_source_seconds\":"; json_number(file, row.infer_source_seconds);
            file << ",\"last_decode_monotonic_s\":"; json_number(file, row.last_decode_time);
            file << ",\"last_infer_monotonic_s\":"; json_number(file, row.last_infer_time);
            file << ",\"last_decoded_age_ms\":"; json_number(file, row.last_decoded_age_ms);
            file << ",\"last_inferred_age_ms\":"; json_number(file, row.last_inferred_age_ms);
            file << ",\"source_lag_ms\":"; json_number(file, row.source_lag_ms);
            file << ",\"read_ms\":"; json_number(file, row.read_ms);
            file << ",\"slow_seconds\":" << row.slow_seconds << ",\"sustained_slow\":" << (row.sustained_slow ? "true" : "false")
                 << ",\"stalled\":" << (row.stalled ? "true" : "false") << ",\"state\":" << quote(observation_state(row)) << '}';
        }
        file << "],\"comparisons\":[";
        for (size_t index = 0; index < observation_selected_.size(); ++index) {
            const auto id = observation_selected_[index];
            if (index) file << ',';
            file << "{\"id\":" << id << ",\"decoded\":"; write_observation_image(file, view.raw_frames[id], now);
            file << ",\"detected\":"; write_observation_image(file, view.frames[id], now); file << '}';
        }
        file << "],\"note\":\"Rolling rates count events, not preview frames. Decode age is not camera/display latency. Local primed pre-clock events retain identity but are excluded from rolling rates.\"}";
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
    void snapshot_files(const cv::Mat& canvas, const std::vector<Frame>& frames, Time now, const ViewSnapshot& view) {
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
            file << "\n  ]";
            if (observation_enabled_) write_observation(file, view, now);
            file << "\n}\n";
            file.close();
        }
        replace_file(status + ".tmp", status);
    }
};

#endif
