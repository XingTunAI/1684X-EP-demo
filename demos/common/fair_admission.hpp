#pragma once
#include <condition_variable>
#include <cstdint>
#include <cstddef>
#include <mutex>

// Bound concurrent processing without allowing one stream to overtake waiters.
// A zero limit preserves unrestricted behavior. The lease must outlive SDK work.
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
    ~AdmissionLease() { admission->release(); }
    AdmissionLease(const AdmissionLease&) = delete;
    AdmissionLease& operator=(const AdmissionLease&) = delete;
};
