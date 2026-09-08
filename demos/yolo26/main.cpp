// One process, one complete business thread per stream. Each thread owns its
// decoder, detector, runtime and buffers. No decoded-frame queue or
// separate inference-worker pool. SDKs may create their own internal threads.
#include "yolo26_det.hpp"
#include "json.hpp"
extern "C" {
#include <libavutil/frame.h>
}
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <cerrno>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
using Time = Clock::time_point;
static volatile std::sig_atomic_t interrupted = 0;
static void on_signal(int) { interrupted = 1; }
static double monotonic_seconds(Time t) { return std::chrono::duration<double>(t.time_since_epoch()).count(); }
static double elapsed(Time a, Time b) { return std::chrono::duration<double>(b - a).count(); }
static double millis(Time a, Time b) { return elapsed(a, b) * 1000.0; }
static Time add_seconds(Time t, double n) { return t + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(n)); }
static std::string trim(const std::string& s) {
    const auto a = s.find_first_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, s.find_last_not_of(" \t\r\n") - a + 1);
}
static bool live_source(const std::string& s) { return s.compare(0, 7, "rtsp://") == 0 || s.compare(0, 8, "rtsps://") == 0; }
static std::string redacted(const std::string& s) {
    auto begin = s.find("://");
    if (begin == std::string::npos) return s;
    auto at = s.find('@', begin + 3), slash = s.find('/', begin + 3);
    if (at != std::string::npos && (slash == std::string::npos || at < slash))
        return s.substr(0, begin + 3) + "[credentials]@" + s.substr(at + 1);
    return s;
}
static std::string stream_name(size_t i) {
    std::ostringstream out; out << "stream_" << std::setfill('0') << std::setw(2) << i; return out.str();
}
static void make_directory(const std::string& p) {
    if (mkdir(p.c_str(), 0755) != 0) throw std::runtime_error("Cannot create fresh directory: " + p);
}
static void json_file(const std::string& p, const json& value) {
    std::ofstream out(p); out.exceptions(std::ios::failbit | std::ios::badbit); out << value.dump(2) << '\n';
}

// Admission controls only concurrency; each source still executes on its own thread.
// FIFO ticket admission prevents a fast stream repeatedly overtaking waiting streams.
class FairAdmission {
    std::mutex lock;
    std::condition_variable changed;
    uint64_t next = 0, serving = 0;
    size_t active = 0, limit;
public:
    explicit FairAdmission(size_t n) : limit(n) {}
    void acquire() {
        if (!limit) return;
        std::unique_lock<std::mutex> guard(lock);
        const auto ticket = next++;
        changed.wait(guard, [&] { return ticket == serving && active < limit; });
        ++active; ++serving; changed.notify_all();
    }
    void release() {
        if (!limit) return;
        std::lock_guard<std::mutex> guard(lock);
        --active; changed.notify_all();
    }
};
class AdmissionLease {
    FairAdmission* admission;
public:
    explicit AdmissionLease(FairAdmission& a) : admission(&a) { admission->acquire(); }
    void release() { if (admission) { admission->release(); admission = nullptr; } }
    ~AdmissionLease() { release(); }
    AdmissionLease(const AdmissionLease&) = delete;
    AdmissionLease& operator=(const AdmissionLease&) = delete;
};

struct Config {
    int active_limit = 0;
    std::string image_path = "yuv";
    double pace_fps = 0;
    int max_frames = 0;
    bool save_frame = false;
    int device = 0, slots = 2;
    double warmup = 5, duration = 30, window = 10;
    float conf = 0.25f;
    std::string model, names, output, eof = "stop";
    std::vector<std::string> sources;
};
static Config arguments(int argc, char** argv) {
    std::map<std::string, std::string> opts;
    const std::vector<std::string> accepted = {"input", "inputs-file", "streams", "slots", "device", "bmodel", "classnames", "output", "warmup", "duration", "window", "conf", "local-eof", "policy", "active-limit", "image-path", "pace-fps", "max-frames", "save-frame"};
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help") {
            std::cout << "yolo26_streams.pcie --input FILE --streams N | --inputs-file FILE\n"
                      << "  --bmodel FILE --classnames FILE --output NEW_DIRECTORY\n"
                      << "  [--device 0 --warmup 5 --duration 30 --window 10]\n"
                      << "  [--local-eof stop|loop|fail --policy all --image-path yuv|bgr]\n"
                      << "  [--conf 0.25 --active-limit 0] (0 = no admission limit)\n"
                      << "  [--slots N] (when supplied, must equal stream count)\n"
                      << "  [--pace-fps 0] (0 = file source FPS; -1 = unpaced local throughput)\n"
                      << "  [--max-frames 0 --save-frame off] (saving requires 1 stream/1 frame)\n"
                      << "RTSP is not software-paced. All decoded frames are processed sequentially.\n";
            std::exit(0);
        }
        if (arg.compare(0, 2, "--")) throw std::runtime_error("Expected --argument");
        auto equals = arg.find('=');
        auto key = arg.substr(2, equals == std::string::npos ? equals : equals - 2);
        if (std::find(accepted.begin(), accepted.end(), key) == accepted.end()) throw std::runtime_error("Unknown argument: " + key);
        std::string val;
        if (equals != std::string::npos) val = arg.substr(equals + 1);
        else if (++i < argc) val = argv[i];
        else throw std::runtime_error("Missing argument value: " + key);
        if (!opts.emplace(key, val).second) throw std::runtime_error("Duplicate argument: " + key);
    }
    auto get = [&](const std::string& key, const std::string& fallback) {
        const auto it = opts.find(key); return it == opts.end() ? fallback : it->second;
    };
    auto integer = [&](const std::string& key, int fallback) {
        const auto value = get(key, std::to_string(fallback)); size_t n = 0;
        int out = std::stoi(value, &n); if (n != value.size()) throw std::runtime_error("Invalid integer: " + key); return out;
    };
    auto number = [&](const std::string& key, double fallback) {
        const auto value = get(key, std::to_string(fallback)); size_t n = 0;
        double out = std::stod(value, &n);
        if (n != value.size() || !std::isfinite(out)) throw std::runtime_error("Invalid number: " + key);
        return out;
    };
    Config c;
    c.device = integer("device", 0); c.slots = integer("slots", 2);
    c.warmup = number("warmup", 5); c.duration = number("duration", 30); c.window = number("window", 10);
    c.conf = static_cast<float>(number("conf", .25));
    c.model = get("bmodel", ""); c.names = get("classnames", ""); c.output = get("output", "");
    c.eof = get("local-eof", "stop");
    c.image_path = get("image-path", "yuv");
    c.pace_fps = number("pace-fps", 0); c.max_frames = integer("max-frames", 0);
    const auto save = get("save-frame", "off");
    if (save != "off" && save != "on") throw std::runtime_error("save-frame must be off or on");
    c.save_frame = save == "on";
    if (c.pace_fps < -1 || (c.pace_fps < 0 && c.pace_fps != -1) || c.max_frames < 0)
        throw std::runtime_error("pace-fps must be -1, 0, or positive; max-frames must be nonnegative");
    if (c.image_path != "bgr" && c.image_path != "yuv") throw std::runtime_error("image-path must be bgr or yuv");
    c.active_limit = integer("active-limit", 0);
    if (c.active_limit < 0 || c.active_limit > 32) throw std::runtime_error("active-limit must be 0..32 (0 disables admission)");
    if (c.device < 0 || c.slots < 1 || c.warmup < 0 || c.duration <= 0 || c.window <= 0 || c.window > c.duration || c.conf < 0 || c.conf > 1)
        throw std::runtime_error("Invalid device/slots/timing/threshold value");
    if (get("policy", "all") != "all") throw std::runtime_error("Only sequential all-frame processing is supported");
    if (c.eof != "stop" && c.eof != "loop" && c.eof != "fail") throw std::runtime_error("Invalid local-eof policy");
    if (c.model.empty() || c.names.empty() || c.output.empty()) throw std::runtime_error("bmodel, classnames and output are required");
    if (access(c.model.c_str(), R_OK) || access(c.names.c_str(), R_OK)) throw std::runtime_error("Cannot read model or class names");
    if (access(c.output.c_str(), F_OK) == 0) throw std::runtime_error("Output directory must not exist");
    const auto input = get("input", ""), inputs_file = get("inputs-file", "");
    if (input.empty() == inputs_file.empty()) throw std::runtime_error("Specify exactly one of input or inputs-file");
    const int streams = integer("streams", input.empty() ? 0 : 1);
    if (!input.empty()) {
        if (streams < 1 || streams > 256) throw std::runtime_error("streams must be 1..256");
        if (live_source(input) && streams > 1) throw std::runtime_error("Use inputs-file with distinct RTSP sources");
        c.sources.assign(streams, input);
    } else {
        std::ifstream file(inputs_file);
        if (!file) throw std::runtime_error("Cannot read inputs-file");
        auto slash = inputs_file.find_last_of('/');
        const auto base = slash == std::string::npos ? "." : inputs_file.substr(0, slash);
        for (std::string line; std::getline(file, line); ) {
            line = trim(line); if (line.empty() || line[0] == '#') continue;
            if (line.find("://") == std::string::npos && line[0] != '/') line = base + "/" + line;
            if (std::find(c.sources.begin(), c.sources.end(), line) != c.sources.end()) throw std::runtime_error("inputs-file requires distinct sources");
            c.sources.push_back(line);
        }
        if (c.sources.empty() || c.sources.size() > 256 || (streams != 0 && static_cast<size_t>(streams) != c.sources.size()))
            throw std::runtime_error("inputs-file must match streams when streams is specified");
    }
    for (const auto& source : c.sources) {
        if (!live_source(source) && source.find("://") != std::string::npos) throw std::runtime_error("Only local files and RTSP are supported");
        if (!live_source(source) && access(source.c_str(), R_OK)) throw std::runtime_error("Cannot read local source");
    }
    if (c.sources.size() > 32) throw std::runtime_error("This single-card demo supports at most 32 streams");
    if (opts.count("slots") && static_cast<size_t>(c.slots) != c.sources.size())
        throw std::runtime_error("One thread per stream: slots, when supplied, must equal streams");
    if (c.save_frame && (c.sources.size() != 1 || c.max_frames != 1 || c.warmup != 0))
        throw std::runtime_error("save-frame requires streams=1, max-frames=1, warmup=0; use a separate correctness run");
    c.slots = static_cast<int>(c.sources.size());
    return c;
}

struct Frame {
    cv::Mat mat;
    size_t stream = 0;
    uint64_t sequence = 0, source_frame = 0, source_loop = 0;
    bool live = false;
    Time due, before_decode, after_decode, ready;
    double backpressure_ms = 0;
};
struct Metric {
    double sum = 0; std::vector<double> values;
    void add(double v) { sum += v; values.push_back(v); }
    json summary() const {
        if (values.empty()) return {{"mean", nullptr}, {"p50", nullptr}, {"p95", nullptr}, {"p99", nullptr}, {"max", nullptr}};
        auto sorted = values; std::sort(sorted.begin(), sorted.end());
        auto percentile = [&](double q) { return sorted[static_cast<size_t>(std::ceil(q * sorted.size())) - 1]; };
        return {{"mean", sum / values.size()}, {"p50", percentile(.5)}, {"p95", percentile(.95)}, {"p99", percentile(.99)}, {"max", sorted.back()}};
    }
};
struct Stream {
    bool done = false, eof_seen = false, frame_limit_seen = false;
    std::string error;
    uint64_t decoded = 0, completed = 0, decoded_measured = 0, completed_measured = 0;
    int width = 0, height = 0;
    double source_fps = 0, pacing_fps = 0;
    Metric decode, bridge, queue_age, service, pipeline, backpressure, lateness, inference, copy, cpu_post, pre;
    std::vector<uint64_t> complete_windows, decode_windows;
    std::ofstream records;
};
struct SlotInfo {
    uint64_t frames = 0, measured = 0; double detect_ms = 0;
    uint64_t host_alloc = 0, device_alloc = 0, copy_bytes = 0;
};
struct Shared {
    Config config;
    FairAdmission admission;
    std::mutex lock;
    std::condition_variable changed;
    bool started = false, failed = false;
    std::string error;
    size_t ready_count = 0;
    Time start, measure_start, end;
    std::vector<std::unique_ptr<Stream>> streams;
    std::vector<SlotInfo> slots;
    explicit Shared(const Config& c) : config(c), admission(c.active_limit), slots(c.slots) {
        const size_t windows = static_cast<size_t>(std::ceil(c.duration / c.window));
        for (size_t i = 0; i < c.sources.size(); ++i) {
            streams.emplace_back(new Stream);
            auto& s = *streams.back(); s.complete_windows.assign(windows, 0); s.decode_windows.assign(windows, 0);
            const auto dir = c.output + "/" + stream_name(i); make_directory(dir);
            s.records.open(dir + "/detections.jsonl"); s.records.exceptions(std::ios::failbit | std::ios::badbit);
        }
    }
    void fail(const std::string& what) {
        std::lock_guard<std::mutex> guard(lock);
        failed = true; if (error.empty()) error = what;
        changed.notify_all();
    }
    bool barrier() {
        std::unique_lock<std::mutex> guard(lock); ++ready_count; changed.notify_all();
        while (!started && !failed && !interrupted) changed.wait_for(guard, std::chrono::milliseconds(100));
        return started && !failed && !interrupted;
    }
    bool measured(Time t) const { return t >= measure_start && t < end; }
    size_t window_index(Time t) const { return static_cast<size_t>(elapsed(measure_start, t) / config.window); }
};

static void configure_capture(cv::VideoCapture& cap, const std::string& input, int device) {
    if (!cap.open(input, cv::CAP_FFMPEG, device) || !cap.isOpened()) throw std::runtime_error("Decoder open failed");
    if (!cap.set(cv::CAP_PROP_OUTPUT_YUV, 1) || cap.get(cv::CAP_PROP_OUTPUT_YUV) != 1)
        throw std::runtime_error("Decoder cannot provide device YUV");
}
static double stage(TimeStamp* ts, const std::string& name) {
    auto found = ts->records_.find(name);
    if (found == ts->records_.end() || found->second->size() != 2) throw std::runtime_error("Missing stage: " + name);
    return std::chrono::duration<double, std::milli>((*found->second)[1] - (*found->second)[0]).count();
}
static void stream_thread(Shared& state, size_t id) {
    auto& stream = *state.streams[id];
    try {
        // No detector, BMRuntime, mutable timestamp or output cache is shared.
        Yolo26_det net(state.config.model, state.config.names, state.config.device, state.config.conf);
        if (net.batch_size != 1) throw std::runtime_error("Only batch-1 models are supported");
        const auto& source = state.config.sources[id];
        const bool live = live_source(source);
        cv::VideoCapture cap; configure_capture(cap, source, state.config.device);
        stream.width = static_cast<int>(cap.get(cv::CAP_PROP_FRAME_WIDTH));
        stream.height = static_cast<int>(cap.get(cv::CAP_PROP_FRAME_HEIGHT));
        stream.source_fps = cap.get(cv::CAP_PROP_FPS);
        if (!live && state.config.pace_fps == 0 && (!std::isfinite(stream.source_fps) || stream.source_fps <= 0))
            throw std::runtime_error("Local source has unknown FPS; pacing is required");
        stream.pacing_fps = live || state.config.pace_fps == -1 ? 0 : state.config.pace_fps == 0 ? stream.source_fps : state.config.pace_fps;
        if (!state.barrier()) return;
        auto& slot = state.slots[id];
        uint64_t sequence = 0, source_frame = 0, source_loop = 0;
        while (!interrupted) {
            if (state.config.max_frames && sequence >= static_cast<uint64_t>(state.config.max_frames)) { stream.frame_limit_seen = true; break; }
            const bool paced = !live && stream.pacing_fps > 0;
            const Time due = paced ? add_seconds(state.start, sequence / stream.pacing_fps) : Clock::now();
            {
                std::unique_lock<std::mutex> guard(state.lock);
                while (paced && !state.failed && !interrupted && Clock::now() < due && Clock::now() < state.end)
                    state.changed.wait_until(guard, std::min(due, state.end));
                if (state.failed || interrupted || Clock::now() >= state.end) break;
            }
            // All image wrappers die at the end of this iteration, before cap.read runs again.
            std::shared_ptr<Frame> frame(new Frame);
            frame->stream = id; frame->sequence = sequence; frame->source_frame = source_frame; frame->source_loop = source_loop;
            frame->live = live; frame->due = due; frame->backpressure_ms = 0;
            frame->before_decode = Clock::now();
            cap >> frame->mat; frame->after_decode = Clock::now();
            if (frame->mat.empty()) {
                if (live) throw std::runtime_error("RTSP returned no frame; reconnect is not implemented");
                if (!source_frame) throw std::runtime_error("Local source returned no frames");
                if (state.config.eof == "fail") throw std::runtime_error("Local EOF before test deadline");
                if (state.config.eof == "stop") { stream.eof_seen = true; break; }
                cap.release(); configure_capture(cap, source, state.config.device);
                ++source_loop; source_frame = 0; continue;
            }
            ++stream.decoded;
            if (state.measured(frame->after_decode)) {
                ++stream.decoded_measured; ++stream.decode_windows.at(state.window_index(frame->after_decode));
            }
            frame->ready = frame->after_decode;
            if (slot.frames % 128 == 0) {
                struct statvfs disk;
                if (statvfs(state.config.output.c_str(), &disk) ||
                    static_cast<double>(disk.f_bavail) * disk.f_frsize < 512.0 * 1024 * 1024)
                    throw std::runtime_error("Less than 512 MiB free; stopping detection");
            }
            const Time admission_begin = Clock::now();
            AdmissionLease lease(state.admission);
            const Time selected = Clock::now();
            cv::Mat device_bgr;
            bm_image image;
            if (state.config.image_path == "yuv") {
                if (!frame->mat.u || !frame->mat.u->frame ||
                    frame->mat.u->frame->colorspace != AVCOL_SPC_BT709 ||
                    frame->mat.u->frame->color_range != AVCOL_RANGE_MPEG)
                    throw std::runtime_error("Direct YUV requires explicit BT709 limited-range source metadata; use --image-path bgr otherwise");
                net.preprocess_csc = CSC_YCbCr2RGB_BT709;
                if (cv::bmcv::toBMI(frame->mat, &image, false) != BM_SUCCESS) throw std::runtime_error("Decoded YUV to BMImage failed");
            } else {
                if (cv::bmcv::toMAT(frame->mat, device_bgr, false) != BM_SUCCESS) throw std::runtime_error("Device YUV to BGR failed");
                if (cv::bmcv::toBMI(device_bgr, &image, false) != BM_SUCCESS) throw std::runtime_error("Mat to BMImage failed");
            }
            struct Cleanup { bm_image& image; ~Cleanup() { bm_image_destroy(image); } } cleanup{image};
            if (state.config.image_path == "yuv" && image.image_format != FORMAT_YUV420P &&
                image.image_format != FORMAT_NV12 && image.image_format != FORMAT_NV21 && image.image_format != FORMAT_COMPRESSED)
                throw std::runtime_error("Direct path requires decoded YUV420/NV12/NV21/compressed image");
            const Time before_detect = Clock::now();
            std::vector<bm_image> images{image}; std::vector<Yolo26BoxVec> boxes;
            if (net.Detect(images, boxes) != 0 || boxes.size() != 1) throw std::runtime_error("Detect failed");
            const Time after_detect = Clock::now();
            lease.release();
            const double preprocess = stage(net.m_ts, "yolo26 preprocess"), inference = stage(net.m_ts, "yolo26 inference");
            const double postprocess = stage(net.m_ts, "yolo26 postprocess"), transfer = stage(net.m_ts, "yolo26 output transfer");
            const double transfer_wait = stage(net.m_ts, "yolo26 transfer wait"), cpu_post = stage(net.m_ts, "yolo26 cpu postprocess");
            for (auto& item : net.m_ts->records_) item.second->clear();
            for (auto& item : net.m_ts->records_bs) item.second->clear();
            json detections = json::array();
            for (const auto& box : boxes[0]) detections.push_back({{"class_id", box.class_id}, {"score", box.score}, {"xyxy", {box.x1, box.y1, box.x2, box.y2}}});
            json record = {{"stream_id", frame->stream}, {"source_id", stream_name(frame->stream)}, {"slot_id", id},
                {"frame", frame->sequence}, {"source_frame_id", frame->source_frame}, {"source_loop", frame->source_loop},
                {"received_monotonic_s", monotonic_seconds(frame->after_decode)}, {"analysis_completed_monotonic_s", monotonic_seconds(after_detect)},
                {"decode_ms", millis(frame->before_decode, frame->after_decode)}, {"image_bridge_ms", millis(selected, before_detect)},
                {"admission_wait_ms", millis(admission_begin, selected)}, {"image_path", state.config.image_path}, {"bm_image_format", static_cast<int>(image.image_format)}, {"active_limit", state.config.active_limit},
                {"queue_age_ms", millis(frame->ready, selected)}, {"backpressure_ms", frame->backpressure_ms},
                {"schedule_lateness_ms", !paced ? json(nullptr) : json(std::max(0.0, millis(frame->due, frame->before_decode)))},
                {"preprocess_ms", preprocess}, {"inference_ms", inference}, {"postprocess_ms", postprocess},
                {"inference_submit_ms", net.inference_submit_ms}, {"inference_sync_ms", net.inference_sync_ms},
                {"input_release_ms", net.input_release_ms}, {"output_copy_ms", net.output_copy_ms},
                {"output_allocation_ms", net.output_allocation_ms}, {"output_transfer_ms", transfer},
                {"transfer_wait_ms", transfer_wait}, {"cpu_postprocess_ms", cpu_post},
                {"service_ms", millis(selected, after_detect)}, {"pipeline_ms", millis(frame->before_decode, after_detect)},
                {"policy", "all"}, {"detections", detections}};
            if (state.config.save_frame && sequence == 0) {
                const std::string prefix = state.config.output + "/saved_frame";
                net.save_frame(prefix);
                const auto& letterbox = net.last_letterbox();
                json_file(prefix + ".json", {{"input_shape", {1, 3, net.input_height(), net.input_width()}},
                    {"input_dtype_bm", net.input_dtype()}, {"input_scale", net.input_scale()},
                    {"output_shape", {1, net.output_rows(), 6}}, {"output_dtype", "float32"},
                    {"output_layout", "xyxy_score_class"}, {"color", "RGB"}, {"nms", false},
                    {"source_width", image.width}, {"source_height", image.height},
                    {"letterbox", {{"gain", letterbox.gain}, {"left", letterbox.left}, {"top", letterbox.top},
                        {"resize_width", letterbox.resize_width}, {"resize_height", letterbox.resize_height}}},
                    {"record", record}});
            }
            // Count a completed frame after its JSON record is written.
            stream.records << record.dump() << '\n';
            if ((frame->sequence + 1) % 25 == 0) stream.records.flush();
            const Time written = Clock::now();
            if (stream.completed != frame->sequence) throw std::runtime_error("Stream output sequence was reordered");
            ++stream.completed; ++slot.frames;
            if (state.measured(written)) {
                ++stream.completed_measured; ++stream.complete_windows.at(state.window_index(written)); ++slot.measured;
                slot.detect_ms += millis(before_detect, after_detect);
                stream.decode.add(millis(frame->before_decode, frame->after_decode)); stream.bridge.add(millis(selected, before_detect));
                stream.queue_age.add(millis(frame->ready, selected)); stream.service.add(millis(selected, after_detect));
                stream.pipeline.add(millis(frame->before_decode, written)); stream.backpressure.add(frame->backpressure_ms);
                if (paced) stream.lateness.add(std::max(0.0, millis(frame->due, frame->before_decode)));
                stream.inference.add(inference); stream.copy.add(net.output_copy_ms); stream.cpu_post.add(cpu_post); stream.pre.add(preprocess);
            }
            ++sequence; ++source_frame;
        }
        slot.host_alloc = net.host_output_allocations; slot.device_alloc = net.device_output_allocations; slot.copy_bytes = net.output_copy_bytes;
    } catch (const std::exception& e) {
        stream.error = e.what(); state.fail("stream " + std::to_string(id) + ": " + e.what());
    }
    stream.done = true;
}

int main(int argc, char** argv) {
    std::signal(SIGINT, on_signal); std::signal(SIGTERM, on_signal);
    try {
        const auto config = arguments(argc, argv); make_directory(config.output);
        Shared state(config);
        json sources = json::array();
        for (size_t i = 0; i < config.sources.size(); ++i) sources.push_back({{"stream_id", i}, {"source_id", stream_name(i)}, {"source", redacted(config.sources[i])}, {"live", live_source(config.sources[i])}});
        json_file(config.output + "/config.json", {{"device", config.device}, {"streams", config.sources.size()}, {"slots", config.slots},
            {"warmup_s", config.warmup}, {"duration_s", config.duration}, {"window_s", config.window}, {"model", config.model},
            {"conf", config.conf}, {"nms", false}, {"output_layout", "xyxy_score_class"}, {"sources", sources}, {"policy", "all"}, {"local_eof", config.eof},
            {"output_buffer", "reuse"}, {"input_buffer", "reuse"}, {"pace_fps", config.pace_fps}, {"max_frames", config.max_frames}, {"save_frame", config.save_frame}, {"max_outstanding_per_stream", 1},
            {"csc_mode", "require_bt709_limited_for_yuv"}, {"image_path", config.image_path}, {"active_limit", config.active_limit}, {"admission_scope", "device_bridge_through_detect"}, {"topology", "one_business_thread_per_stream"}, {"business_threads", config.sources.size()}, {"model_instances", config.slots}, {"draw", false}, {"encode", false}});
        std::vector<std::thread> threads;
        for (size_t i = 0; i < config.sources.size(); ++i)
            threads.emplace_back(stream_thread, std::ref(state), i);
        {
            std::unique_lock<std::mutex> guard(state.lock);
            while (!state.failed && !interrupted && state.ready_count < config.sources.size())
                state.changed.wait_for(guard, std::chrono::milliseconds(100));
            state.start = Clock::now(); state.measure_start = add_seconds(state.start, config.warmup); state.end = add_seconds(state.measure_start, config.duration);
            state.started = true; state.changed.notify_all();
        }
        std::cout << "yolo26_streams started streams=" << config.sources.size() << " slots=" << config.slots << " output=" << config.output << std::endl;
        for (auto& thread : threads) thread.join();
        const Time finished = Clock::now();
        const double measured_seconds = std::max(0.0, elapsed(state.measure_start, std::min(finished, state.end)));
        bool eof_seen = false, complete = true, all_frame_limits = config.max_frames > 0;
        double total_fps = 0, minimum_fps = std::numeric_limits<double>::infinity();
        json streams = json::array(), slot_rows = json::array();
        std::ofstream csv(config.output + "/streams.csv"); csv.exceptions(std::ios::failbit | std::ios::badbit);
        csv << "stream_id,decoded,completed,decoded_measured,completed_measured,decoded_fps,completed_fps,queue_mean_ms,queue_p95_ms,pipeline_p95_ms,schedule_lateness_max_ms,source_ended,error\n";
        for (size_t i = 0; i < state.streams.size(); ++i) {
            auto& s = *state.streams[i]; s.records.close(); eof_seen = eof_seen || s.eof_seen;
            all_frame_limits = all_frame_limits && s.frame_limit_seen;
            complete = complete && s.decoded == s.completed && s.error.empty();
            const double fps = measured_seconds > 0 ? s.completed_measured / measured_seconds : 0;
            const double decode_fps = measured_seconds > 0 ? s.decoded_measured / measured_seconds : 0;
            total_fps += fps; minimum_fps = std::min(minimum_fps, fps);
            json windows = json::array();
            for (size_t w = 0; w < s.complete_windows.size(); ++w) {
                const double duration = std::min(config.window, measured_seconds - w * config.window);
                if (duration <= 0) break;
                windows.push_back({{"index", w}, {"seconds", duration}, {"completed", s.complete_windows[w]},
                    {"decoded", s.decode_windows[w]}, {"completed_fps", s.complete_windows[w] / duration}, {"decoded_fps", s.decode_windows[w] / duration}});
            }
            json result = {{"stream_id", i}, {"source_id", stream_name(i)}, {"decoded", s.decoded}, {"completed", s.completed},
                {"decoded_measured", s.decoded_measured}, {"completed_measured", s.completed_measured}, {"decoded_fps", decode_fps}, {"completed_fps", fps},
                {"source_width", s.width}, {"source_height", s.height}, {"source_fps", std::isfinite(s.source_fps) ? json(s.source_fps) : json(nullptr)},
                {"source_ended", s.eof_seen}, {"frame_limit_reached", s.frame_limit_seen}, {"pacing_fps", s.pacing_fps}, {"error", s.error}, {"unprocessed_decoded_frames", s.decoded - s.completed}, {"policy_drops", 0}, {"decoder_drops", nullptr},
                {"windows", windows}, {"decode_ms", s.decode.summary()}, {"image_bridge_ms", s.bridge.summary()},
                {"queue_age_ms", s.queue_age.summary()}, {"service_ms", s.service.summary()}, {"pipeline_to_record_ms", s.pipeline.summary()},
                {"backpressure_ms", s.backpressure.summary()}, {"schedule_lateness_ms", s.lateness.summary()},
                {"preprocess_ms", s.pre.summary()}, {"inference_ms", s.inference.summary()}, {"output_copy_ms", s.copy.summary()}, {"cpu_postprocess_ms", s.cpu_post.summary()}};
            streams.push_back(result); json_file(config.output + "/" + stream_name(i) + "/summary.json", result);
            csv << i << ',' << s.decoded << ',' << s.completed << ',' << s.decoded_measured << ',' << s.completed_measured << ',' << decode_fps << ',' << fps << ','
                << s.queue_age.summary()["mean"] << ',' << s.queue_age.summary()["p95"] << ',' << s.pipeline.summary()["p95"] << ','
                << s.lateness.summary()["max"] << ',' << (s.eof_seen ? 1 : 0) << ',' << json(s.error).dump() << '\n';
        }
        for (size_t i = 0; i < state.slots.size(); ++i) {
            const auto& s = state.slots[i]; slot_rows.push_back({{"slot_id", i}, {"frames", s.frames}, {"measured_frames", s.measured},
                {"measured_detect_ms", s.detect_ms}, {"host_output_allocations", s.host_alloc}, {"device_output_allocations", s.device_alloc}, {"output_copy_bytes", s.copy_bytes}});
        }
        const std::string status = state.failed ? "failed" : interrupted ? "interrupted" : !complete ? "incomplete_records" : all_frame_limits ? "frame_limit" : eof_seen ? "source_ended" : measured_seconds < config.duration - .01 ? "incomplete_duration" : "measured";
        json_file(config.output + "/summary.json", {{"status", status}, {"error", state.error}, {"measured_seconds", measured_seconds},
            {"total_completed_fps", total_fps}, {"minimum_stream_fps", minimum_fps}, {"streams", streams}, {"slots", slot_rows},
            {"records_complete", complete}, {"drain_seconds", std::max(0.0, elapsed(state.end, finished))}, {"application_drops", 0}, {"decoder_drops", nullptr},
            {"acceptance", nullptr}, {"note", "Measured completion FPS is not a realtime acceptance verdict. RTSP internal buffering/drops and camera latency are unmeasured."}});
        std::cout << "status=" << status << " total_completed_fps=" << total_fps << " minimum_stream_fps=" << minimum_fps << std::endl;
        return status == "measured" || status == "frame_limit" ? 0 : 2;
    } catch (const std::exception& e) { std::cerr << "YOLO26_PIPELINE_ERROR: " << e.what() << std::endl; return 2; }
}
