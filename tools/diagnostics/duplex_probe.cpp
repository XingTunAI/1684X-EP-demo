// Independent payload buffers: serial vs simultaneous PCIe directions.
#include "bmlib_runtime.h"
#include "json.hpp"
#include <chrono>
#include <thread>
#include <atomic>
#include <vector>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <cmath>
#include <mutex>
#include <condition_variable>
using Clock = std::chrono::steady_clock;
static double now() { return std::chrono::duration<double>(Clock::now().time_since_epoch()).count(); }
static void check(bool ok) { if (!ok) throw std::runtime_error("SDK transfer/allocation or validation failed"); }
struct Buffer {
    bm_handle_t handle = nullptr;
    bm_device_mem_t mem{};
    bool allocated = false;
    Buffer(int device, size_t size) {
        check(bm_dev_request(&handle, device) == BM_SUCCESS);
        allocated = bm_malloc_device_byte(handle, &mem, size) == BM_SUCCESS;
        if (!allocated) { bm_dev_free(handle); handle = nullptr; check(false); }
    }
    ~Buffer() { if (allocated) bm_free_device(handle, mem); if (handle) bm_dev_free(handle); }
};
int main(int argc, char** argv) {
    try {
        if (argc != 7) throw std::runtime_error("Usage: duplex_probe device bytes seconds warmup serial|duplex|paired|upload|download output.json");
        int device = std::stoi(argv[1]); size_t bytes = std::stoull(argv[2]);
        double seconds = std::stod(argv[3]), warmup = std::stod(argv[4]); std::string mode = argv[5];
        check(device >= 0 && bytes > 0 && bytes <= 64*1024*1024 && std::isfinite(seconds) && seconds > 0 &&
              std::isfinite(warmup) && warmup >= 0 && (mode == "serial" || mode == "duplex" || mode == "paired" || mode == "upload" || mode == "download"));
        Buffer up(device, bytes), down(device, bytes);
        std::vector<unsigned char> source(bytes), reference(bytes), host(bytes), verify(bytes);
        for (size_t i=0; i<bytes; ++i) { source[i]=(i*17+3)%251; reference[i]=(i*11+7)%253; }
        check(bm_memcpy_s2d(down.handle, down.mem, reference.data()) == BM_SUCCESS);
        check(bm_memcpy_s2d(up.handle, up.mem, source.data()) == BM_SUCCESS);
        std::atomic<bool> failed(false);
        size_t uploads=0, downloads=0; double up_ms=0, down_ms=0;
        const double start = now()+0.1, formal=start+warmup, end=formal+seconds;
        auto operation = [&](bool upload) {
            double t=now();
            auto rc=upload ? bm_memcpy_s2d(up.handle, up.mem, source.data()) : bm_memcpy_d2s(down.handle, host.data(), down.mem);
            double finish=now();
            if (rc != BM_SUCCESS) failed=true;
            // Only fully contained calls are counted; identical fixed window for both directions.
            if (t>=formal && finish<=end) {
                if (upload) { ++uploads; up_ms+=(finish-t)*1000; }
                else { ++downloads; down_ms+=(finish-t)*1000; }
            }
        };
        auto loop = [&](bool upload) {
            while(now()<start) std::this_thread::sleep_for(std::chrono::milliseconds(1));
            while(now()<end && !failed) operation(upload);
        };
        if (mode=="duplex") { std::thread a(loop,true), b(loop,false); a.join(); b.join(); }
        else if (mode=="paired") {
            std::mutex mutex; std::condition_variable cv; bool requested=false, done=false, stop=false;
            // Equal numbers of sends/receives, with a persistent upload worker.
            std::thread sender([&]() {
                std::unique_lock<std::mutex> lock(mutex);
                for (;;) {
                    cv.wait(lock,[&] { return stop || requested; });
                    if(stop) return;
                    requested=false; lock.unlock(); operation(true); lock.lock(); done=true; cv.notify_all();
                }
            });
            while(now()<start) std::this_thread::sleep_for(std::chrono::milliseconds(1));
            while(now()<end && !failed) {
                { std::lock_guard<std::mutex> lock(mutex); done=false; requested=true; cv.notify_all(); }
                operation(false);
                std::unique_lock<std::mutex> lock(mutex); cv.wait(lock,[&] { return done; });
            }
            { std::lock_guard<std::mutex> lock(mutex); stop=true; cv.notify_all(); }
            sender.join();
        }
        else if (mode=="serial") {
            while(now()<start) std::this_thread::sleep_for(std::chrono::milliseconds(1));
            while(now()<end && !failed) { operation(true); if(now()<end) operation(false); }
        } else loop(mode=="upload");
        check(!failed);
        check(bm_memcpy_d2s(up.handle, verify.data(), up.mem)==BM_SUCCESS && verify==source);
        check(bm_memcpy_d2s(down.handle, host.data(), down.mem)==BM_SUCCESS && host==reference);
        nlohmann::json result={{"mode",mode},{"device",device},{"bytes_per_call",bytes},{"seconds",seconds},
          {"measurement_start_monotonic",formal},{"measurement_end_monotonic",end},
          {"uploads",uploads},{"downloads",downloads},{"upload_MB_s",uploads*double(bytes)/seconds/1e6},
          {"download_MB_s",downloads*double(bytes)/seconds/1e6},
          {"upload_mean_ms",uploads ? up_ms/uploads : 0},{"download_mean_ms",downloads ? down_ms/downloads : 0},
          {"data_verified",true},{"independent_handles_and_buffers",true}};
        std::ofstream f(argv[6]); f<<result.dump(2)<<'\n'; check(bool(f));
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
