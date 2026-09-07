// One independent decode/analysis pipeline per process; encoding is optional.
#include "yolov8_det.hpp"
#include "json.hpp"
#include <chrono>
#include <csignal>
#include <cmath>
#include <thread>
#include <stdexcept>
#include <sys/statvfs.h>
#include <unistd.h>

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
static volatile std::sig_atomic_t stopped = 0;
static void stop_handler(int) { stopped = 1; }
static double monotonic_seconds(Clock::time_point t) {
    return std::chrono::duration<double>(t.time_since_epoch()).count();
}
static double ms(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}
static double stage_ms(TimeStamp* ts, const std::string& name) {
    const auto found = ts->records_.find(name);
    if (found == ts->records_.end() || found->second->size() != 2)
        throw std::runtime_error("Missing inference stage timestamps: " + name);
    const auto& times = *found->second;
    return std::chrono::duration<double, std::milli>(times[1] - times[0]).count();
}

int main(int argc, char** argv) {
    std::signal(SIGTERM, stop_handler);
    std::signal(SIGINT, stop_handler);
    cv::CommandLineParser args(argc, argv,
        "{help h||}{input||local video}{bmodel||model}{classnames||names}"
        "{device|0|device ID}{output||existing unique output directory}"
        "{mode|encode|analysis or encode}"
        "{image_path|bgr|bgr baseline, yuv, or device-bgr}"
        "{transfer_lock||optional per-run output transfer gate file}"
        "{output_buffer|baseline|baseline or reuse}"
        "{fps|25|source/analysis/output target FPS}{bitrate|4000|output kbps}"
        "{conf|0.25|confidence}{nms|0.7|NMS}");
    if (args.has("help")) { args.printMessage(); return 0; }
    try {
        const auto input = args.get<std::string>("input");
        const auto model = args.get<std::string>("bmodel");
        const auto names = args.get<std::string>("classnames");
        const auto output = args.get<std::string>("output");
        const auto mode = args.get<std::string>("mode");
        if (mode != "analysis" && mode != "encode") throw std::runtime_error("Invalid mode");
        const bool encode = mode == "encode";
        const auto image_path = args.get<std::string>("image_path");
        if (image_path != "bgr" && image_path != "yuv" && image_path != "device-bgr") throw std::runtime_error("Invalid image_path");
        const bool yuv = image_path != "bgr";
        if (encode && yuv) throw std::runtime_error("YUV path currently supports analysis only");
        const int device = args.get<int>("device"), bitrate = args.get<int>("bitrate");
        const double fps = args.get<double>("fps");
        if (!args.check() || device < 0 || bitrate < 1 || !std::isfinite(fps) || fps <= 0)
            throw std::runtime_error("Invalid arguments");
        for (const auto& file : {input, model, names})
            if (file.empty() || access(file.c_str(), R_OK))
                throw std::runtime_error("Cannot read input/model/class names");
        if (output.empty() || access(output.c_str(), W_OK))
            throw std::runtime_error("Output directory must exist and be writable");
        if (access((output + "/output.mp4").c_str(), F_OK) == 0)
            throw std::runtime_error("Refusing to overwrite existing video");
        YoloV8_det net(model, names, device, args.get<float>("conf"), args.get<float>("nms"));
        const auto output_buffer = args.get<std::string>("output_buffer");
        if (output_buffer != "baseline" && output_buffer != "reuse") throw std::runtime_error("Invalid output_buffer");
        net.reuse_output_buffers = output_buffer == "reuse";
        net.transfer_lock_path = args.get<std::string>("transfer_lock");
        if (net.batch_size != 1) throw std::runtime_error("This baseline requires a 1-batch model");
        cv::VideoCapture cap(input, cv::CAP_FFMPEG, device);
        if (!cap.isOpened()) throw std::runtime_error("Decoder open failed");
        if (yuv && (!cap.set(cv::CAP_PROP_OUTPUT_YUV, 1) || cap.get(cv::CAP_PROP_OUTPUT_YUV) != 1))
            throw std::runtime_error("Decoder does not support YUV output");
        const int width = static_cast<int>(cap.get(cv::CAP_PROP_FRAME_WIDTH));
        const int height = static_cast<int>(cap.get(cv::CAP_PROP_FRAME_HEIGHT));
        if (width != 1920 || height != 1080 || std::abs(cap.get(cv::CAP_PROP_FPS) - fps) > 0.05)
            throw std::runtime_error("Video must match 1920x1080 and target FPS");
        cv::VideoWriter writer;
        const std::string enc = "bitrate=" + std::to_string(bitrate) + ":gop_preset=2";
        if (encode && !writer.open(output + "/output.mp4", cv::VideoWriter::fourcc('H','2','6','4'),
                         fps, cv::Size(width, height), enc, true, device))
            throw std::runtime_error("Encoder open failed");
        std::ofstream detections(output + "/detections.jsonl");
        detections.exceptions(std::ios::failbit | std::ios::badbit);
        const auto start = Clock::now();
        size_t frame = 0, pass_frames = 0;
        while (!stopped) {
            const auto due = start + std::chrono::duration_cast<Clock::duration>(
                std::chrono::duration<double>(frame / fps));
            std::this_thread::sleep_until(due);
            if (stopped) break;
            if (frame % 25 == 0) {
                struct statvfs disk;
                if (statvfs(output.c_str(), &disk) ||
                    static_cast<double>(disk.f_bavail) * disk.f_frsize < 512.0 * 1024 * 1024)
                    throw std::runtime_error("Less than 512 MiB free; stopping output");
            }
            const auto before_decode = Clock::now();
            cv::Mat mat;
            cap >> mat;
            if (mat.empty()) {
                if (!pass_frames) throw std::runtime_error("Video returned no frames");
                cap.release();
                if (!cap.open(input, cv::CAP_FFMPEG, device))
                    throw std::runtime_error("Cannot reopen video at EOF");
                if (yuv && !cap.set(cv::CAP_PROP_OUTPUT_YUV, 1))
                    throw std::runtime_error("Cannot restore YUV output after EOF");
                pass_frames = 0;
                continue;
            }
            ++pass_frames;
            const auto after_decode = Clock::now();
            cv::Mat device_bgr;
            if (image_path == "device-bgr" && cv::bmcv::toMAT(mat, device_bgr, false) != BM_SUCCESS)
                throw std::runtime_error("Device YUV to BGR failed");
            cv::Mat& inference_mat = image_path == "device-bgr" ? device_bgr : mat;
            bm_image image;
            // The YUV decoder owns current device pixels; no CPU pixel edits occurred.
            // Keep mat alive until Detect completes and the borrowed bm_image is destroyed.
            if (cv::bmcv::toBMI(inference_mat, &image, !yuv) != BM_SUCCESS)
                throw std::runtime_error("Mat to bm_image failed");
            struct Cleanup { bm_image& image; ~Cleanup() { bm_image_destroy(image); } } cleanup{image};
            const auto after_bridge = Clock::now();
            if (image.width != width || image.height != height)
                throw std::runtime_error("Unexpected decoded image dimensions");
            if (frame == 0) std::cout << "image_path=" << image_path << " bm_image_format=" << image.image_format << std::endl;
            std::vector<bm_image> images{image};
            std::vector<YoloV8BoxVec> boxes;
            if (net.Detect(images, boxes) != 0 || boxes.size() != 1)
                throw std::runtime_error("Inference failed");
            const auto after_detect = Clock::now();
            const double pre_ms = stage_ms(net.m_ts, "yolov8 preprocess");
            const double infer_ms = stage_ms(net.m_ts, "yolov8 inference");
            const double post_ms = stage_ms(net.m_ts, "yolov8 postprocess");
            const double transfer_wait_ms = stage_ms(net.m_ts, "yolov8 transfer wait");
            const double transfer_ms = stage_ms(net.m_ts, "yolov8 output transfer");
            const double cpu_post_ms = stage_ms(net.m_ts, "yolov8 cpu postprocess");
            // Consume this frame's timestamps; avoid accumulating/erasing 4000 entries per stage.
            for (auto& item : net.m_ts->records_) item.second->clear();
            for (auto& item : net.m_ts->records_bs) item.second->clear();
            if (encode) net.draw_result(mat, boxes[0]);
            const auto after_draw = Clock::now();
            if (encode) writer.write(mat); // Submission only; runner verifies video after close.
            const auto after_write = Clock::now();
            json list = json::array();
            for (const auto& box : boxes[0])
                list.push_back({{"class_id", box.class_id}, {"score", box.score},
                                {"xyxy", {box.x1, box.y1, box.x2, box.y2}}});
            json record = {{"frame", frame}, {"source_time_s", frame / fps},
                           {"completed_monotonic_s", monotonic_seconds(after_write)},
                           {"decode_ms", ms(before_decode, after_decode)},
                           {"analysis_ms", ms(after_decode, after_detect)},
                           {"image_bridge_ms", ms(after_decode, after_bridge)},
                           {"preprocess_ms", pre_ms}, {"inference_ms", infer_ms}, {"postprocess_ms", post_ms},
                           {"output_allocation_ms", net.output_allocation_ms < 0 ? json(nullptr) : json(net.output_allocation_ms)},
                           {"output_copy_ms", net.output_copy_ms < 0 ? json(nullptr) : json(net.output_copy_ms)},
                           {"transfer_wait_ms", transfer_wait_ms}, {"output_transfer_ms", transfer_ms}, {"cpu_postprocess_ms", cpu_post_ms},
                           {"draw_ms", encode ? json(ms(after_detect, after_draw)) : json(nullptr)},
                           {"encode_submit_ms", encode ? json(ms(after_draw, after_write)) : json(nullptr)},
                           {"service_ms", ms(before_decode, after_write)},
                           {"schedule_lateness_ms", std::max(0.0, ms(due, before_decode))},
                           {"detections", list}};
            detections << record.dump() << '\n';
            ++frame;
            if (frame % 25 == 0) detections.flush();
            std::cout << "frame=" << frame << std::endl;
        }
        writer.release();
        cap.release();
        detections.close();
        std::ofstream summary(output + "/worker_summary.json");
        summary << json({{"frames_analyzed", frame}, {"frames_submitted", encode ? frame : 0},
                         {"mode", mode},
                         {"image_path", image_path}, {"output_buffer", output_buffer},
                         {"host_output_allocations", net.host_output_allocations},
                         {"device_output_allocations", net.device_output_allocations},
                         {"output_copy_bytes", net.output_copy_bytes},
                         {"device", device}, {"bitrate_kbps", bitrate},
                         {"target_fps", fps}, {"shutdown", "signal"}}).dump(2);
        if (!summary) throw std::runtime_error("Cannot save worker summary");
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "PIPELINE_ERROR: " << e.what() << std::endl;
        return 1;
    }
}
