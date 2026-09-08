// One process, one complete business thread per stream. Each thread owns its
// decoder, detector, auxiliary runtime and buffers. No decoded-frame queue or
// separate inference-worker pool. SDKs may create their own internal threads.
#include "yolov8_det.hpp"
#include "json.hpp"
#include "score_gate_plan.hpp"
#include "wall_renderer.hpp"
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

struct Config {
    int device = 0, slots = 2;
    double warmup = 5, duration = 30, window = 10;
    float conf = 0.25f, nms = 0.7f;
    std::string model, names, output, eof = "stop", output_buffer = "baseline";
    std::string score_gate = "off", score_gate_model, cpu_post = "dense";
    std::vector<std::string> sources;
};
static Config arguments(int argc, char** argv) {
    std::map<std::string, std::string> opts;
    const std::vector<std::string> accepted = {"input", "inputs-file", "streams", "slots", "device", "bmodel", "classnames", "output", "warmup", "duration", "window", "conf", "nms", "local-eof", "output-buffer", "policy", "score-gate", "score-gate-model", "cpu-post"};
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help") {
            std::cout << "hdmi_wall.pcie --input FILE --streams N | --inputs-file FILE\n"
                      << "  --bmodel FILE --classnames FILE --output NEW_DIRECTORY\n"
                      << "  [--device 0 --warmup 5 --duration 30 --window 10]\n"
                      << "  [--local-eof stop|loop|fail --output-buffer baseline|reuse --policy all]\n"
                      << "  [--score-gate off|on --score-gate-model AUXILIARY_BMODEL]\n"
                      << "  [--cpu-post dense|selected] (selected requires score-gate on)\n"
                      << "RTSP is not software-paced. Local files use their source FPS. No frame skipping.\n";
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
    c.conf = static_cast<float>(number("conf", .25)); c.nms = static_cast<float>(number("nms", .7));
    c.model = get("bmodel", ""); c.names = get("classnames", ""); c.output = get("output", "");
    c.eof = get("local-eof", "stop"); c.output_buffer = get("output-buffer", "baseline");
    c.score_gate = get("score-gate", "off"); c.score_gate_model = get("score-gate-model", "");
    c.cpu_post = get("cpu-post", "dense");
    if ((c.cpu_post != "dense" && c.cpu_post != "selected") || (c.cpu_post == "selected" && c.score_gate != "on"))
        throw std::runtime_error("cpu-post must be dense or selected; selected requires score-gate on");
    if (c.score_gate != "off" && c.score_gate != "on") throw std::runtime_error("Invalid score-gate option");
    if (c.score_gate == "on" && (c.score_gate_model.empty() || access(c.score_gate_model.c_str(), R_OK) || c.conf <= 0))
        throw std::runtime_error("Score gate on requires readable score-gate-model and positive confidence");
    if (c.device < 0 || c.slots < 1 || c.warmup < 0 || c.duration <= 0 || c.window <= 0 || c.window > c.duration || c.conf < 0 || c.conf > 1 || c.nms < 0 || c.nms > 1)
        throw std::runtime_error("Invalid device/slots/timing/threshold value");
    if (get("policy", "all") != "all") throw std::runtime_error("This prototype supports only explicit all-frame processing");
    if (c.eof != "stop" && c.eof != "loop" && c.eof != "fail") throw std::runtime_error("Invalid local-eof policy");
    if (c.output_buffer != "baseline" && c.output_buffer != "reuse") throw std::runtime_error("Invalid output-buffer");
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
    if (c.sources.size() > 32) throw std::runtime_error("This single-card experiment supports at most 32 streams");
    if (opts.count("slots") && static_cast<size_t>(c.slots) != c.sources.size())
        throw std::runtime_error("One thread per stream: slots, when supplied, must equal streams");
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
static json gate_json(const ScoreGateMetrics& g) {
    return {{"execute_calls", g.kernel_calls}, {"score_read_calls", g.score_calls}, {"row_read_calls", g.row_calls}, {"full_read_calls", g.full_calls},
        {"selected_rows", g.selected_rows}, {"ranges", g.ranges}, {"score_bytes", g.score_bytes}, {"row_bytes", g.row_bytes}, {"full_bytes", g.full_bytes},
        {"total_d2h_bytes", g.total_bytes()}, {"execute_ms", g.kernel_ms}, {"submit_ms", g.submit_ms}, {"sync_ms", g.sync_ms},
        {"score_read_ms", g.score_ms}, {"row_read_ms", g.row_ms}, {"full_read_ms", g.full_ms}, {"host_zero_ms", g.host_zero_ms},
        {"fallback", g.fallback}, {"fallback_reason", g.fallback_reason}};
}
struct GateSummary {
    uint64_t frames = 0, execute_calls = 0, score_calls = 0, row_calls = 0, full_calls = 0;
    uint64_t selected_rows = 0, ranges = 0, score_bytes = 0, row_bytes = 0, full_bytes = 0;
    Metric execute, score_read, row_read, full_read, host_zero;
    std::map<std::string, uint64_t> fallbacks;
    void add(const ScoreGateMetrics& g) {
        ++frames; execute_calls += g.kernel_calls; score_calls += g.score_calls; row_calls += g.row_calls; full_calls += g.full_calls;
        selected_rows += g.selected_rows; ranges += g.ranges; score_bytes += g.score_bytes; row_bytes += g.row_bytes; full_bytes += g.full_bytes;
        execute.add(g.kernel_ms); score_read.add(g.score_ms); row_read.add(g.row_ms); full_read.add(g.full_ms); host_zero.add(g.host_zero_ms);
        if (g.fallback) ++fallbacks[g.fallback_reason];
    }
    json summary() const {
        return {{"frames", frames}, {"execute_calls", execute_calls}, {"score_read_calls", score_calls}, {"row_read_calls", row_calls},
            {"full_read_calls", full_calls}, {"selected_rows", selected_rows}, {"ranges", ranges}, {"score_bytes", score_bytes},
            {"row_bytes", row_bytes}, {"full_bytes", full_bytes}, {"total_d2h_bytes", score_bytes + row_bytes + full_bytes},
            {"execute_ms", execute.summary()}, {"score_read_ms", score_read.summary()}, {"row_read_ms", row_read.summary()},
            {"full_read_ms", full_read.summary()}, {"host_zero_ms", host_zero.summary()}, {"fallback_reasons", fallbacks}};
    }
};
struct Stream {
    bool done = false, eof_seen = false;
    std::string error;
    uint64_t decoded = 0, completed = 0, decoded_measured = 0, completed_measured = 0;
    int width = 0, height = 0;
    double source_fps = 0;
    Metric decode, bridge, queue_age, service, pipeline, backpressure, lateness, inference, copy, cpu_post, pre;
    GateSummary gate;
    std::vector<uint64_t> complete_windows, decode_windows;
    std::ofstream records;
};
struct SlotInfo {
    uint64_t frames = 0, measured = 0; double detect_ms = 0;
    uint64_t host_alloc = 0, device_alloc = 0, copy_bytes = 0, gate_alloc = 0;
    GateSummary gate;
};
struct Shared {
    Config config;
    WallRenderer wall;
    std::mutex lock;
    std::condition_variable changed;
    bool started = false, failed = false;
    std::string error;
    size_t ready_count = 0;
    Time start, measure_start, end;
    std::vector<std::unique_ptr<Stream>> streams;
    std::vector<SlotInfo> slots;
    explicit Shared(const Config& c) : config(c),
        wall(c.sources.size(), c.output, c.device, c.model.substr(c.model.find_last_of('/') + 1)), slots(c.slots) {
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

// Only 256x144 BGR pixels cross PCIe for preview. Each stream owns and reuses
// its image, handle and host buffer; decoding and inference retain full input.
class Thumbnail {
    bm_handle_t handle_ = nullptr;
    bm_image image_{};
    bool created_ = false;
    std::vector<unsigned char> pixels_;
public:
    explicit Thumbnail(int device) : pixels_(256 * 144 * 3) {
        if (bm_dev_request(&handle_, device) != BM_SUCCESS)
            throw std::runtime_error("Preview device handle failed");
        int stride[] = {256 * 3};
        if (bm_image_create(handle_, 144, 256, FORMAT_BGR_PACKED, DATA_TYPE_EXT_1N_BYTE,
                            &image_, stride) != BM_SUCCESS) {
            bm_dev_free(handle_); handle_ = nullptr;
            throw std::runtime_error("Preview image creation failed");
        }
        created_ = true;
        if (bm_image_alloc_dev_mem(image_, BMCV_IMAGE_FOR_OUT) != BM_SUCCESS) {
            bm_image_destroy(image_); created_ = false; bm_dev_free(handle_); handle_ = nullptr;
            throw std::runtime_error("Preview image allocation failed");
        }
    }
    ~Thumbnail() { if (created_) bm_image_destroy(image_); if (handle_) bm_dev_free(handle_); }
    cv::Mat render(bm_image source, const YoloV8BoxVec& boxes) {
        if (bmcv_image_vpp_convert(handle_, 1, source, &image_) != BM_SUCCESS)
            throw std::runtime_error("Preview VPP resize failed");
        void* buffers[] = {pixels_.data()};
        if (bm_image_copy_device_to_host(image_, buffers) != BM_SUCCESS)
            throw std::runtime_error("Preview thumbnail readback failed");
        cv::Mat host(144, 256, CV_8UC3, pixels_.data());
        const double sx = 256.0 / source.width, sy = 144.0 / source.height;
        for (const auto& box : boxes) {
            if (!std::isfinite(box.x1) || !std::isfinite(box.y1) || !std::isfinite(box.x2) || !std::isfinite(box.y2)) continue;
            const int x1 = std::max(0, std::min(255, static_cast<int>(box.x1 * sx)));
            const int y1 = std::max(0, std::min(143, static_cast<int>(box.y1 * sy)));
            const int x2 = std::max(0, std::min(255, static_cast<int>(box.x2 * sx)));
            const int y2 = std::max(0, std::min(143, static_cast<int>(box.y2 * sy)));
            if (x2 <= x1 || y2 <= y1) continue;
            const cv::Scalar color = box.class_id == 0 ? cv::Scalar(0, 220, 255) : cv::Scalar(80, 255, 100);
            cv::rectangle(host, cv::Point(x1, y1), cv::Point(x2, y2), color, 1);
        }
        return host;
    }
};

static void stream_thread(Shared& state, size_t id) {
    auto& stream = *state.streams[id];
    try {
        // No detector, BMRuntime, mutable timestamp or output cache is shared.
        YoloV8_det net(state.config.model, state.config.names, state.config.device, state.config.conf, state.config.nms);
        net.reuse_output_buffers = state.config.output_buffer == "reuse";
        net.score_gate_enabled = state.config.score_gate == "on";
        net.score_gate_sparse_cpu = state.config.cpu_post == "selected";
        net.score_gate_model = state.config.score_gate_model;
        net.initialize_score_gate(); // Off returns without opening an auxiliary model.
        Thumbnail thumbnail(state.config.device);
        if (net.batch_size != 1) throw std::runtime_error("Only batch-1 models are supported");
        const auto& source = state.config.sources[id];
        const bool live = live_source(source);
        cv::VideoCapture cap; configure_capture(cap, source, state.config.device);
        stream.width = static_cast<int>(cap.get(cv::CAP_PROP_FRAME_WIDTH));
        stream.height = static_cast<int>(cap.get(cv::CAP_PROP_FRAME_HEIGHT));
        stream.source_fps = cap.get(cv::CAP_PROP_FPS);
        if (!live && (!std::isfinite(stream.source_fps) || stream.source_fps <= 0))
            throw std::runtime_error("Local source has unknown FPS; pacing is required");
        if (!state.barrier()) return;
        auto& slot = state.slots[id];
        uint64_t sequence = 0, source_frame = 0, source_loop = 0;
        Time last_preview = Time::min(), rate_start = Clock::now();
        uint64_t rate_count = 0;
        double process_fps = 0;
        while (!interrupted) {
            if (state.wall.failed()) throw std::runtime_error("HDMI renderer: " + state.wall.error());
            const Time due = live ? Clock::now() : add_seconds(state.start, sequence / stream.source_fps);
            {
                std::unique_lock<std::mutex> guard(state.lock);
                while (!live && !state.failed && !interrupted && Clock::now() < due && Clock::now() < state.end)
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
                    throw std::runtime_error("Less than 512 MiB free; stopping experiment");
            }
            const Time selected = Clock::now();
            cv::Mat device_bgr;
            if (cv::bmcv::toMAT(frame->mat, device_bgr, false) != BM_SUCCESS) throw std::runtime_error("Device YUV to BGR failed");
            bm_image image;
            if (cv::bmcv::toBMI(device_bgr, &image, false) != BM_SUCCESS) throw std::runtime_error("Mat to BMImage failed");
            struct Cleanup { bm_image& image; ~Cleanup() { bm_image_destroy(image); } } cleanup{image};
            const Time before_detect = Clock::now();
            std::vector<bm_image> images{image}; std::vector<YoloV8BoxVec> boxes;
            if (net.Detect(images, boxes) != 0 || boxes.size() != 1) throw std::runtime_error("Detect failed");
            const Time after_detect = Clock::now();
            ++rate_count;
            if (elapsed(rate_start, after_detect) >= 1) {
                process_fps = rate_count / elapsed(rate_start, after_detect);
                rate_start = after_detect; rate_count = 0;
            }
            double preview_ms = 0;
            if (last_preview == Time::min() || elapsed(last_preview, after_detect) >= .1) {
                const auto preview_start = Clock::now();
                const auto host = thumbnail.render(image, boxes[0]);
                state.wall.submit(id, host, sequence + 1, boxes[0].size(), process_fps);
                last_preview = Clock::now(); preview_ms = millis(preview_start, last_preview);
            }
            const double preprocess = stage(net.m_ts, "yolov8 preprocess"), inference = stage(net.m_ts, "yolov8 inference");
            const double postprocess = stage(net.m_ts, "yolov8 postprocess"), transfer = stage(net.m_ts, "yolov8 output transfer");
            const double transfer_wait = stage(net.m_ts, "yolov8 transfer wait"), cpu_post = stage(net.m_ts, "yolov8 cpu postprocess");
            for (auto& item : net.m_ts->records_) item.second->clear();
            for (auto& item : net.m_ts->records_bs) item.second->clear();
            json detections = json::array();
            for (const auto& box : boxes[0]) detections.push_back({{"class_id", box.class_id}, {"score", box.score}, {"xyxy", {box.x1, box.y1, box.x2, box.y2}}});
            json record = {{"stream_id", frame->stream}, {"source_id", stream_name(frame->stream)}, {"slot_id", id},
                {"frame", frame->sequence}, {"source_frame_id", frame->source_frame}, {"source_loop", frame->source_loop},
                {"received_monotonic_s", monotonic_seconds(frame->after_decode)}, {"analysis_completed_monotonic_s", monotonic_seconds(after_detect)},
                {"decode_ms", millis(frame->before_decode, frame->after_decode)}, {"image_bridge_ms", millis(selected, before_detect)},
                {"queue_age_ms", millis(frame->ready, selected)}, {"backpressure_ms", frame->backpressure_ms},
                {"schedule_lateness_ms", frame->live ? json(nullptr) : json(std::max(0.0, millis(frame->due, frame->before_decode)))},
                {"preprocess_ms", preprocess}, {"inference_ms", inference}, {"postprocess_ms", postprocess},
                {"inference_submit_ms", net.inference_submit_ms}, {"inference_sync_ms", net.inference_sync_ms},
                {"input_release_ms", net.input_release_ms}, {"output_copy_ms", net.output_copy_ms},
                {"output_allocation_ms", net.output_allocation_ms}, {"output_transfer_ms", transfer},
                {"transfer_wait_ms", transfer_wait}, {"cpu_postprocess_ms", cpu_post},
                {"score_gate", state.config.score_gate}, {"score_gate_metrics", gate_json(net.score_gate_metrics)},
                {"cpu_post", state.config.cpu_post},
                {"preview_ms", preview_ms},
                {"service_ms", millis(selected, after_detect)}, {"pipeline_ms", millis(frame->before_decode, after_detect)},
                {"policy", "all"}, {"detections", detections}};
            // Output counts follow successful JSON insertion, as in the baseline.
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
                if (!frame->live) stream.lateness.add(std::max(0.0, millis(frame->due, frame->before_decode)));
                stream.inference.add(inference); stream.copy.add(net.output_copy_ms); stream.cpu_post.add(cpu_post); stream.pre.add(preprocess);
                stream.gate.add(net.score_gate_metrics); slot.gate.add(net.score_gate_metrics);
            }
            ++sequence; ++source_frame;
        }
        slot.host_alloc = net.host_output_allocations; slot.device_alloc = net.device_output_allocations; slot.copy_bytes = net.output_copy_bytes;
        slot.gate_alloc = net.score_gate_buffer_allocations;
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
            {"conf", config.conf}, {"nms", config.nms}, {"sources", sources}, {"policy", "all"}, {"local_eof", config.eof},
            {"image_path", "device-bgr"}, {"output_buffer", config.output_buffer}, {"max_outstanding_per_stream", 1},
            {"score_gate", config.score_gate}, {"score_gate_model", config.score_gate_model}, {"score_gate_backend", "bmrt_auxiliary_reducemax"},
            {"cpu_post", config.cpu_post},
            {"gate_max_ranges", score_gate::max_ranges}, {"gate_max_rows", score_gate::max_rows}, {"gate_full_byte_fraction_percent", score_gate::byte_fraction_percent},
            {"topology", "one_business_thread_per_stream"}, {"business_threads", config.sources.size()}, {"model_instances", config.slots}, {"draw", true}, {"encode", false},
            {"display", "HDMI via system ffplay"}, {"preview_max_fps", 10}, {"preview_thumbnail", {256, 144}}, {"wall_resolution", {1920, 1080}}});
        std::thread display([&]() { state.wall.run(); if (state.wall.failed()) state.fail("HDMI renderer: " + state.wall.error()); });
        std::vector<std::thread> threads;
        try {
            for (size_t i = 0; i < config.sources.size(); ++i)
                threads.emplace_back(stream_thread, std::ref(state), i);
        } catch (const std::exception& e) {
            // Release already-created workers from the start barrier, then join
            // them below. Never destroy a joinable thread on allocation failure.
            state.fail(std::string("Worker thread creation failed: ") + e.what());
        }
        {
            std::unique_lock<std::mutex> guard(state.lock);
            while (!state.failed && !interrupted && state.ready_count < config.sources.size())
                state.changed.wait_for(guard, std::chrono::milliseconds(100));
            state.start = Clock::now(); state.measure_start = add_seconds(state.start, config.warmup); state.end = add_seconds(state.measure_start, config.duration);
            state.started = true; state.changed.notify_all();
        }
        std::cout << "hdmi_wall started streams=" << config.sources.size() << " slots=" << config.slots << " output=" << config.output << std::endl;
        for (auto& thread : threads) thread.join();
        state.wall.stop(); display.join();
        const Time finished = Clock::now();
        const double measured_seconds = std::max(0.0, elapsed(state.measure_start, std::min(finished, state.end)));
        bool eof_seen = false, complete = true;
        double total_fps = 0, minimum_fps = std::numeric_limits<double>::infinity();
        json streams = json::array(), slot_rows = json::array();
        std::ofstream csv(config.output + "/streams.csv"); csv.exceptions(std::ios::failbit | std::ios::badbit);
        csv << "stream_id,decoded,completed,decoded_measured,completed_measured,decoded_fps,completed_fps,queue_mean_ms,queue_p95_ms,pipeline_p95_ms,schedule_lateness_max_ms,source_ended,error\n";
        for (size_t i = 0; i < state.streams.size(); ++i) {
            auto& s = *state.streams[i]; s.records.close(); eof_seen = eof_seen || s.eof_seen;
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
                {"source_ended", s.eof_seen}, {"error", s.error}, {"unprocessed_decoded_frames", s.decoded - s.completed}, {"policy_drops", 0}, {"decoder_drops", nullptr},
                {"windows", windows}, {"decode_ms", s.decode.summary()}, {"image_bridge_ms", s.bridge.summary()},
                {"queue_age_ms", s.queue_age.summary()}, {"service_ms", s.service.summary()}, {"pipeline_to_record_ms", s.pipeline.summary()},
                {"backpressure_ms", s.backpressure.summary()}, {"schedule_lateness_ms", s.lateness.summary()},
                {"preprocess_ms", s.pre.summary()}, {"inference_ms", s.inference.summary()}, {"output_copy_ms", s.copy.summary()}, {"cpu_postprocess_ms", s.cpu_post.summary()},
                {"score_gate_measured", s.gate.summary()}};
            streams.push_back(result); json_file(config.output + "/" + stream_name(i) + "/summary.json", result);
            csv << i << ',' << s.decoded << ',' << s.completed << ',' << s.decoded_measured << ',' << s.completed_measured << ',' << decode_fps << ',' << fps << ','
                << s.queue_age.summary()["mean"] << ',' << s.queue_age.summary()["p95"] << ',' << s.pipeline.summary()["p95"] << ','
                << s.lateness.summary()["max"] << ',' << (s.eof_seen ? 1 : 0) << ',' << json(s.error).dump() << '\n';
        }
        for (size_t i = 0; i < state.slots.size(); ++i) {
            const auto& s = state.slots[i]; slot_rows.push_back({{"slot_id", i}, {"frames", s.frames}, {"measured_frames", s.measured},
                {"measured_detect_ms", s.detect_ms}, {"host_output_allocations", s.host_alloc}, {"device_output_allocations", s.device_alloc}, {"output_copy_bytes", s.copy_bytes},
                {"gate_output_allocations", s.gate_alloc}, {"score_gate_measured", s.gate.summary()}});
        }
        const std::string status = state.failed ? "failed" : interrupted ? "interrupted" : !complete ? "incomplete_records" : eof_seen ? "source_ended" : measured_seconds < config.duration - .01 ? "incomplete_duration" : "measured";
        json_file(config.output + "/summary.json", {{"status", status}, {"error", state.error}, {"measured_seconds", measured_seconds},
            {"total_completed_fps", total_fps}, {"minimum_stream_fps", minimum_fps}, {"streams", streams}, {"slots", slot_rows},
            {"records_complete", complete}, {"drain_seconds", std::max(0.0, elapsed(state.end, finished))}, {"application_drops", 0}, {"decoder_drops", nullptr},
            {"acceptance", nullptr}, {"note", "Measured completion FPS is not a realtime acceptance verdict. RTSP internal buffering/drops and camera latency are unmeasured."}});
        std::cout << "status=" << status << " total_completed_fps=" << total_fps << " minimum_stream_fps=" << minimum_fps << std::endl;
        return status == "measured" ? 0 : 2;
    } catch (const std::exception& e) { std::cerr << "HDMI_WALL_ERROR: " << e.what() << std::endl; return 2; }
}
