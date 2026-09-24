// Isolate BMRuntime compute and device-to-host output reads. No video FPS.
#include "bmruntime_interface.h"
#include "bmlib_runtime.h"
#include "json.hpp"
#include "output_read.hpp"
#include <chrono>
#include <thread>
#include <fstream>
#include <vector>
#include <stdexcept>
#include <iostream>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <condition_variable>
#include <functional>

using Clock = std::chrono::steady_clock;
using json = nlohmann::json;
static double now() { return std::chrono::duration<double>(Clock::now().time_since_epoch()).count(); }
static void check(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }

// Persistent worker: do not include thread creation on every frame.
class CopyWorker {
    std::mutex mutex;
    std::condition_variable cv;
    bool stop = false, pending = false, done = true;
    std::function<double()> job;
    double elapsed = 0;
    std::exception_ptr error;
    std::thread worker;
public:
    CopyWorker() : worker([this]() {
        std::unique_lock<std::mutex> lock(mutex);
        for (;;) {
            cv.wait(lock, [this] { return stop || pending; });
            if (stop) return;
            auto task = job; pending = false;
            lock.unlock();
            double result = 0; std::exception_ptr failure;
            try { result = task(); } catch (...) { failure = std::current_exception(); }
            lock.lock(); elapsed = result; error = failure; done = true; cv.notify_all();
        }
    }) {}
    ~CopyWorker() { { std::lock_guard<std::mutex> lock(mutex); stop = true; cv.notify_all(); } worker.join(); }
    void submit(std::function<double()> task) {
        std::lock_guard<std::mutex> lock(mutex);
        check(done, "Previous copy still running"); job = task; done = false; pending = true; cv.notify_all();
    }
    double wait() {
        std::unique_lock<std::mutex> lock(mutex); cv.wait(lock, [this] { return done; });
        if (error) std::rethrow_exception(error); return elapsed;
    }
};

struct Runtime {
    bm_handle_t handle = nullptr;
    void* runtime = nullptr;
    bm_tensor_t input{}, output{}, alternate{};
    bm_handle_t copy_handle = nullptr;
    bool input_allocated = false, output_allocated = false, alternate_allocated = false;
    ~Runtime() {
        if (alternate_allocated) bm_free_device(handle, alternate.device_mem);
        if (copy_handle) bm_dev_free(copy_handle);
        if (output_allocated) bm_free_device(handle, output.device_mem);
        if (input_allocated) bm_free_device(handle, input.device_mem);
        if (runtime) bmrt_destroy(runtime);
        if (handle) bm_dev_free(handle);
    }
};

int main(int argc, char** argv) {
    try {
        check(argc == 8 || argc == 9, "Usage: inference_probe device model mode warmup duration gate_file output.json [chunk_bytes]");
        const long long chunk = argc == 9 ? std::stoll(argv[8]) : 0;
        check(chunk >= 0 && chunk <= 64*1024*1024, "Invalid chunk size");
        const int device = std::stoi(argv[1]);
        const std::string mode = argv[3], gate = argv[6], out = argv[7];
        const double warmup = std::stod(argv[4]), duration = std::stod(argv[5]);
        check(device >= 0 && std::isfinite(warmup) && warmup >= 0 &&
              std::isfinite(duration) && duration > 0, "Invalid timing/device");
        check(mode == "compute" || mode == "copy" || mode == "compute-copy" || mode == "overlap", "Invalid mode");
        Runtime r;
        check(bm_dev_request(&r.handle, device) == BM_SUCCESS, "Device open failed");
        r.runtime = bmrt_create(r.handle);
        check(r.runtime && bmrt_load_bmodel(r.runtime, argv[2]), "Model load failed");
        check(bmrt_get_network_number(r.runtime) == 1, "Requires one network");
        const char** names = nullptr;
        bmrt_get_network_names(r.runtime, &names);
        const std::string network = names[0];
        free(names);
        const auto* net = bmrt_get_network_info(r.runtime, network.c_str());
        check(net && !net->is_dynamic && net->stage_num == 1 && net->input_num == 1 &&
              net->output_num == 1 && net->stages[0].input_shapes[0].dims[0] == 1 &&
              net->input_dtypes[0] == BM_FLOAT32 && net->output_dtypes[0] == BM_FLOAT32,
              "Requires static batch-1 model with one FP32 input/output");
        r.input_allocated = bmrt_tensor(&r.input, r.runtime, net->input_dtypes[0], net->stages[0].input_shapes[0]);
        r.output_allocated = bmrt_tensor(&r.output, r.runtime, net->output_dtypes[0], net->stages[0].output_shapes[0]);
        check(r.input_allocated && r.output_allocated, "Tensor allocation failed");
        if (mode == "overlap") {
            r.alternate_allocated = bmrt_tensor(&r.alternate, r.runtime, net->output_dtypes[0], net->stages[0].output_shapes[0]);
            check(r.alternate_allocated && bm_dev_request(&r.copy_handle, device) == BM_SUCCESS, "Overlap buffer/handle failed");
        }
        const size_t input_bytes = bmrt_tensor_bytesize(&r.input);
        const size_t output_bytes = bmrt_tensor_bytesize(&r.output);
        std::vector<unsigned char> zero(input_bytes, 0), host(output_bytes), reference(output_bytes);
        check(bm_memcpy_s2d(r.handle, r.input.device_mem, zero.data()) == BM_SUCCESS, "Input upload failed");
        auto launch = [&]() {
            check(bmrt_launch_tensor_ex(r.runtime, network.c_str(), &r.input, 1, &r.output, 1, true, false), "Launch failed");
        };
        auto sync = [&]() { check(bm_thread_sync(r.handle) == BM_SUCCESS, "Sync failed"); };
        auto copy = [&]() {
            check(read_output(r.handle, host.data(), r.output.device_mem, output_bytes, chunk) == BM_SUCCESS,
                  "Output copy failed");
        };
        // Seed output even for copy-only mode; comparison uses the same zero input.
        launch(); sync();
        check(bm_memcpy_d2s_partial(r.handle, reference.data(), r.output.device_mem, output_bytes) == BM_SUCCESS,
              "Baseline reference copy failed");
        copy();
        check(host == reference, "Selected copy differs from unchunked reference");
        if (mode == "overlap") {
            check(bmrt_launch_tensor_ex(r.runtime, network.c_str(), &r.input, 1, &r.alternate, 1, true, false), "Alternate launch failed");
            sync();
        }
        { std::ofstream ready(out + ".ready"); ready << "ready\n"; check(bool(ready), "Cannot write ready file"); }
        const double wait_begin = now();
        double start = 0;
        while (!start) {
            std::ifstream in(gate);
            if (in) in >> start;
            check(now() - wait_begin < 120, "Start barrier timeout");
            if (!start) std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        check(std::isfinite(start) && start > now(), "Missed start barrier");
        while (now() < start) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        const double measure_begin = start + warmup, measure_end = measure_begin + duration;
        size_t iterations = 0;
        CopyWorker copy_worker;
        double submit_sum = 0, sync_sum = 0, copy_sum = 0, measured_start = 0, measured_end = 0;
        while (now() < measure_end) {
            const double begin = now();
            if (mode == "overlap") {
                // Read the previous output while computing the next into separate memory.
                copy_worker.submit([&]() {
                    const double t = now();
                    check(read_output(r.copy_handle, host.data(), r.alternate.device_mem, output_bytes, chunk) == BM_SUCCESS, "Overlap copy failed");
                    return now() - t;
                });
            }
            if (mode != "copy") launch();
            const double submitted = now();
            if (mode != "copy") sync();
            const double synced = now();
            double overlap_copy = 0;
            if (mode == "overlap") { overlap_copy = copy_worker.wait(); std::swap(r.output, r.alternate); }
            else if (mode != "compute") copy();
            const double copied = now();
            // Ignore an iteration crossing the warmup boundary; include final overshoot in elapsed time.
            if (begin >= measure_begin) {
                if (!iterations) measured_start = begin;
                ++iterations;
                submit_sum += submitted - begin;
                sync_sum += synced - submitted;
                copy_sum += mode == "overlap" ? overlap_copy : copied - synced;
                measured_end = copied;
            }
        }
        check(iterations > 0, "No measured iterations");
        copy();
        check(host == reference, "Repeated output differs from reference");
        const double elapsed = measured_end - measured_start;
        json result = {{"mode", mode}, {"device", device}, {"iterations", iterations},
            {"measurement_start_monotonic", measured_start}, {"measurement_end_monotonic", measured_end},
            {"seconds", elapsed}, {"iterations_per_second", iterations / elapsed},
            {"input_bytes", input_bytes}, {"output_bytes", output_bytes},
            {"copy_chunk_bytes", chunk},
            {"submit_mean_ms", mode == "copy" ? json(nullptr) : json(submit_sum * 1000 / iterations)},
            {"sync_mean_ms", mode == "copy" ? json(nullptr) : json(sync_sum * 1000 / iterations)},
            {"copy_mean_ms", mode == "compute" ? json(nullptr) : json(copy_sum * 1000 / iterations)},
            {"output_matches_reference", true}, {"input_kind", "resident_zero_tensor"},
            {"buffers", mode == "overlap" ? "resident_input_double_output_separate_copy_handle" : "resident_input_and_output"},
            {"overlap_scope", mode == "overlap" ? "Previous fixed-input output read overlaps next compute; not PCIe bidirectional traffic" : "none"}};
        std::ofstream file(out);
        file << result.dump(2) << '\n';
        check(bool(file), "Cannot write results");
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "INFERENCE_PROBE_ERROR: " << e.what() << '\n';
        return 1;
    }
}
