// Single process, independent device contexts, one video worker per device.
#include "yolov8_det.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <unistd.h>

static volatile std::sig_atomic_t stopped = 0;
static void stop_handler(int) { stopped = 1; }

int main(int argc, char** argv) {
    cv::CommandLineParser args(argc, argv,
        "{help h||}{devices|0,1,2|device IDs}"
        "{input||local video}{bmodel||model}{classnames||class names}"
        "{seconds|60|minimum processing duration; 0 means one pass}"
        "{conf_thresh|0.25|confidence}{nms_thresh|0.7|NMS}");
    if (args.has("help")) { args.printMessage(); return 0; }
    const auto input = args.get<std::string>("input");
    const auto model = args.get<std::string>("bmodel");
    const auto names = args.get<std::string>("classnames");
    const int seconds = args.get<int>("seconds");
    const float conf = args.get<float>("conf_thresh");
    const float nms = args.get<float>("nms_thresh");
    std::vector<int> devices;
    try {
        if (!args.check() || seconds < 0) throw std::runtime_error("Invalid arguments");
        for (const auto& path : {input, model, names})
            if (path.empty() || access(path.c_str(), R_OK))
                throw std::runtime_error("Cannot read file: " + path);
        std::stringstream list(args.get<std::string>("devices"));
        std::string item;
        std::set<int> unique;
        while (std::getline(list, item, ',')) {
            size_t used = 0;
            int id = std::stoi(item, &used);
            if (used != item.size() || id < 0 || !unique.insert(id).second)
                throw std::runtime_error("Invalid or duplicate device ID");
            devices.push_back(id);
        }
        if (devices.empty()) throw std::runtime_error("No devices selected");
    } catch (const std::exception& e) {
        std::cerr << e.what() << std::endl;
        return 2;
    }
    std::signal(SIGINT, stop_handler);
    std::signal(SIGTERM, stop_handler);
    std::mutex gate_mutex, print_mutex;
    std::condition_variable gate;
    bool go = false;
    size_t ready = 0;
    std::atomic<bool> failed{false};
    std::atomic<int> active{0}, max_active{0};
    std::chrono::steady_clock::time_point start;
    auto log = [&](const std::string& text) {
        std::lock_guard<std::mutex> lock(print_mutex);
        std::cout << text << std::endl;
    };
    auto worker = [&](int id) {
        try {
            // Each worker owns its BMRuntime, handle, decoder and profiling data.
            YoloV8_det net(model, names, id, conf, nms);
            cv::VideoCapture cap(input, cv::CAP_FFMPEG, id);
            if (!cap.isOpened()) throw std::runtime_error("Cannot open video");
            if (net.batch_size < 1) throw std::runtime_error("Invalid model batch size");
            log("READY pid=" + std::to_string(getpid()) + " dev_id=" + std::to_string(id));
            {
                std::unique_lock<std::mutex> lock(gate_mutex);
                ++ready;
                gate.notify_all();
                gate.wait(lock, [&] { return go || failed.load() || stopped; });
            }
            if (failed || stopped) return;
            size_t frames = 0, pass_frames = 0;
            bool done = false;
            while (!done && !failed && !stopped) {
                std::vector<cv::Mat> mats;
                std::vector<bm_image> images;
                // Same ownership convention as the official video sample.
                struct Cleanup {
                    std::vector<bm_image>& images;
                    ~Cleanup() { for (auto& image : images) bm_image_destroy(image); }
                } cleanup{images};
                bool eof = false;
                while (mats.size() < static_cast<size_t>(net.batch_size)) {
                    cv::Mat mat;
                    cap >> mat;
                    if (mat.empty()) { eof = true; break; }
                    mats.push_back(mat);
                    bm_image image;
                    cv::bmcv::toBMI(mats.back(), &image);
                    images.push_back(image);
                }
                if (!images.empty()) {
                    std::vector<YoloV8BoxVec> boxes;
                    const int current = ++active;
                    int peak = max_active.load();
                    while (peak < current && !max_active.compare_exchange_weak(peak, current)) {}
                    int ret;
                    try { ret = net.Detect(images, boxes); }
                    catch (...) { --active; throw; }
                    --active;
                    if (ret != 0) throw std::runtime_error("Detect failed");
                    frames += images.size();
                    pass_frames += images.size();
                }
                const double elapsed = std::chrono::duration<double>(
                    std::chrono::steady_clock::now() - start).count();
                done = seconds > 0 && elapsed >= seconds;
                if (eof) {
                    if (!pass_frames) throw std::runtime_error("No frames decoded on this pass");
                    if (seconds == 0) done = true;
                    if (!done) {
                        cap.release();
                        if (!cap.open(input, cv::CAP_FFMPEG, id))
                            throw std::runtime_error("Cannot reopen video");
                        pass_frames = 0;
                    }
                }
            }
            const double elapsed = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - start).count();
            log("SUMMARY pid=" + std::to_string(getpid()) + " dev_id=" +
                std::to_string(id) + " frames=" + std::to_string(frames) +
                " seconds=" + std::to_string(elapsed) +
                " pipeline_fps=" + std::to_string(frames / elapsed));
        } catch (const std::exception& e) {
            failed = true;
            gate.notify_all();
            log("ERROR dev_id=" + std::to_string(id) + " " + e.what());
        } catch (...) {
            failed = true;
            gate.notify_all();
            log("ERROR dev_id=" + std::to_string(id) + " unknown exception");
        }
    };
    std::vector<std::thread> threads;
    try { for (int id : devices) threads.emplace_back(worker, id); }
    catch (const std::exception& e) { failed = true; log(e.what()); gate.notify_all(); }
    size_t ready_count = 0;
    {
        std::unique_lock<std::mutex> lock(gate_mutex);
        while (ready != devices.size() && !failed && !stopped)
            gate.wait_for(lock, std::chrono::milliseconds(100));
        start = std::chrono::steady_clock::now();
        ready_count = ready;
        go = true;
    }
    log("START pid=" + std::to_string(getpid()) + " workers=" + std::to_string(ready_count));
    gate.notify_all();
    for (auto& thread : threads) thread.join();
    log("MAX_OVERLAPPING_DETECT_CALLS=" + std::to_string(max_active.load()));
    return failed ? 1 : (stopped ? 130 : 0);
}
