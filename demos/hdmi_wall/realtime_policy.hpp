#ifndef HDMI_REALTIME_POLICY_HPP
#define HDMI_REALTIME_POLICY_HPP

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>

namespace realtime {
using Clock = std::chrono::steady_clock;
using Time = Clock::time_point;

inline Time next_inference(Time selected, double fps) {
    if (fps <= 0) return selected;
    const long double period = 1.0L / static_cast<long double>(fps);
    const long double remaining =
        std::chrono::duration<long double>(Time::max().time_since_epoch()).count() -
        std::chrono::duration<long double>(selected.time_since_epoch()).count();
    if (period >= remaining) return Time::max();
    return selected + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<long double>(period));
}

inline bool expired(Time origin, Time now, double maximum_age_ms) {
    return maximum_age_ms > 0 &&
        std::chrono::duration<double, std::milli>(now - origin).count() > maximum_age_ms;
}

// Exactly one pending owner, separate from the frame currently in inference.
// Replacing a pending frame immediately releases its device surface. There is
// no producer backpressure and no catch-up burst after a slow inference.
template<class T> class LatestSlot {
public:
    struct Counters {
        uint64_t overwritten = 0, stale = 0, shutdown = 0;
        unsigned high_watermark = 0;
        uint64_t drops() const { return overwritten + stale + shutdown; }
    };

    void publish(std::unique_ptr<T> value) {
        std::lock_guard<std::mutex> guard(lock_);
        if (finished_) { ++counts_.shutdown; return; }
        if (pending_) ++counts_.overwritten;
        pending_ = std::move(value);
        counts_.high_watermark = 1;
        changed_.notify_one();
    }

    // While rate limited, leave the owner in the slot so new arrivals can
    // replace it. Never reserve an old frame and then sleep before inference.
    template<class Stop>
    std::unique_ptr<T> take(Time not_before, Time deadline, Stop stop) {
        std::unique_lock<std::mutex> guard(lock_);
        for (;;) {
            const auto now = Clock::now();
            if (stop() || now >= deadline) return nullptr;
            if (pending_ && now >= not_before) return std::move(pending_);
            if (finished_ && !pending_) return nullptr;
            auto wake = std::min(deadline, now + std::chrono::milliseconds(50));
            if (not_before > now) wake = std::min(wake, not_before);
            changed_.wait_until(guard, wake);
        }
    }

    void discard_stale() {
        std::lock_guard<std::mutex> guard(lock_);
        ++counts_.stale;
    }
    void finish() {
        std::lock_guard<std::mutex> guard(lock_);
        finished_ = true;
        changed_.notify_all();
    }
    void discard_pending() {
        std::lock_guard<std::mutex> guard(lock_);
        if (pending_) { ++counts_.shutdown; pending_.reset(); }
    }
    Counters counters() const {
        std::lock_guard<std::mutex> guard(lock_);
        return counts_;
    }

private:
    mutable std::mutex lock_;
    std::condition_variable changed_;
    std::unique_ptr<T> pending_;
    bool finished_ = false;
    Counters counts_;
};
} // namespace realtime
#endif
