#pragma once
// Opt-in attribution only. No tracefs controls, scheduling or image policy are
// changed here. The supervisor owns tracing and the short enable windows.
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <pthread.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>

namespace diagnostic_trace {
struct Context {
    int stream = -1;
    long tid = 0;
    const char* role = "unknown";
    unsigned long long sequence = 0;
    bool selected = false;
};
inline Context& context() { static thread_local Context c; return c; }

class Session {
    bool configured_ = false;
    unsigned long long streams_ = 0;
    std::string directory_;
    int marker_ = -1;
    std::mutex map_mutex_;
    std::ofstream map_;
    std::ofstream samples_;
    std::atomic<unsigned long long> counters_[32][4];
    std::atomic<bool> enabled_{false}, stop_{false};
    std::atomic<unsigned long long> written_{0}, errors_{0};
    std::thread control_;
    void snapshot() {
        const double now = std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
        samples_ << std::setprecision(15) << "{\"monotonic\":" << now << ",\"streams\":[";
        for (int i=0;i<32;++i) {
            if (i) samples_ << ',';
            samples_ << "{\"id\":" << i << ",\"decoded\":" << counters_[i][0].load()
                     << ",\"stale_publish\":" << counters_[i][1].load()
                     << ",\"stale_select\":" << counters_[i][2].load()
                     << ",\"inferred\":" << counters_[i][3].load() << '}';
        }
        samples_ << "]}\n"; samples_.flush();
        if (!samples_) ++errors_;
    }
public:
    enum Counter { Decoded=0, StalePublish=1, StaleSelect=2, Inferred=3 };
    Session() { for (auto& stream : counters_) for (auto& c : stream) c.store(0); }
    void count(int stream, Counter kind) {
        if (configured_ && stream >= 0 && stream < 32) ++counters_[stream][kind];
    }
    void start() {
        const char* dir = std::getenv("HDMI_DIAG_DIR");
        if (!dir || !*dir) return;
        if (configured_) throw std::runtime_error("Diagnostic session already initialized");
        directory_ = dir;
        const char* selection = std::getenv("HDMI_DIAG_STREAMS");
        std::stringstream ids(selection ? selection : "0,8,16,24");
        std::string value;
        while (std::getline(ids, value, ',')) {
            if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
                throw std::runtime_error("Invalid HDMI_DIAG_STREAMS");
            const unsigned long n = std::stoul(value);
            if (n >= 32) throw std::runtime_error("Diagnostic stream must be below 32");
            streams_ |= 1ull << n;
        }
        if (!streams_ || mkdir(directory_.c_str(), 0755) != 0)
            throw std::runtime_error("Diagnostics require selected streams and a fresh directory");
        marker_ = open("/sys/kernel/debug/tracing/trace_marker", O_WRONLY | O_CLOEXEC);
        if (marker_ < 0) throw std::runtime_error("Cannot open diagnostic trace_marker");
        map_.open((directory_+"/threads.jsonl").c_str());
        if (!map_) throw std::runtime_error("Cannot create diagnostic thread map");
        samples_.open((directory_+"/counters.jsonl").c_str());
        if (!samples_) throw std::runtime_error("Cannot create diagnostic counters");
        { std::ofstream gate(directory_+"/enable"); gate << "0\n"; }
        configured_ = true;
        control_ = std::thread([this]() {
            unsigned tick = 0;
            while (!stop_.load()) {
                int value = 0;
                { std::ifstream gate(directory_+"/enable"); gate >> value; }
                enabled_.store(value == 1, std::memory_order_relaxed);
                if (tick++ % 10 == 0) snapshot();
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
            enabled_.store(false, std::memory_order_relaxed);
            snapshot();
        });
    }
    bool configured() const { return configured_; }
    bool enabled() const { return enabled_.load(std::memory_order_relaxed); }
    void register_thread(int id, const char* role) {
        if (!configured_) return;
        Context& c = context();
        c.stream = id; c.role = role; c.tid = syscall(SYS_gettid);
        c.selected = id >= 0 && id < 32 && (streams_ & (1ull << id));
        char name[16]; std::snprintf(name,sizeof(name),"hw%02d_%.6s",id,role);
        const int named = pthread_setname_np(pthread_self(), name);
        std::lock_guard<std::mutex> guard(map_mutex_);
        map_ << "{\"stream\":" << id << ",\"tid\":" << c.tid
             << ",\"role\":\"" << role << "\",\"selected\":" << (c.selected ? "true" : "false")
             << ",\"name_result\":" << named << "}\n";
        map_.flush();
        if (!map_) throw std::runtime_error("Diagnostic thread map write failed");
    }
    void mark(const char* phase, unsigned long long token, bool begin) noexcept {
        const Context& c = context();
        char text[192];
        const int length = std::snprintf(text,sizeof(text),
            "HWPHASE sid=%d tid=%ld role=%s phase=%s token=%llu edge=%c\n",
            c.stream,c.tid,c.role,phase,token,begin ? 'B' : 'E');
        if (length <= 0 || length >= static_cast<int>(sizeof(text))) { ++errors_; return; }
        ssize_t result;
        do { result = write(marker_,text,static_cast<size_t>(length)); } while (result < 0 && errno == EINTR);
        if (result == length) ++written_; else ++errors_;
    }
    ~Session() {
        stop_.store(true);
        if (control_.joinable()) control_.join();
        if (marker_ >= 0) close(marker_);
        if (configured_) {
            std::ofstream report(directory_+"/diagnostics.json");
            report << "{\"marker_writes\":" << written_.load()
                   << ",\"marker_errors\":" << errors_.load()
                   << ",\"stream_mask\":" << streams_
                   << ",\"control_poll_ms\":50,\"scope\":\"opt-in phase attribution, not free of measurement overhead\"}\n";
        }
    }
};
inline Session& session() { static Session s; return s; }
class Scope {
    const char* phase_;
    unsigned long long token_ = 0;
    bool active_ = false;
public:
    explicit Scope(const char* phase) : phase_(phase) {
        Session& s = session();
        if (s.configured() && context().selected && s.enabled()) {
            active_ = true; token_ = ++context().sequence;
            s.mark(phase_,token_,true);
        }
    }
    ~Scope() { if (active_) session().mark(phase_,token_,false); }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
};
} // namespace diagnostic_trace
