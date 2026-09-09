#include "../realtime_policy.hpp"
#include <atomic>
#include <iostream>
#include <stdexcept>
#include <thread>

static void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
using realtime::Clock;
using realtime::Time;
using realtime::LatestSlot;

struct Frame {
    static std::atomic<int> alive;
    int id;
    explicit Frame(int value) : id(value) { ++alive; }
    ~Frame() { --alive; }
};
std::atomic<int> Frame::alive(0);
static std::unique_ptr<Frame> frame(int id) { return std::unique_ptr<Frame>(new Frame(id)); }
static Time later(int milliseconds = 1000) { return Clock::now() + std::chrono::milliseconds(milliseconds); }
static bool never_stop() { return false; }

static void ownership_and_overload() {
    LatestSlot<Frame> slot;
    slot.publish(frame(0));
    auto in_flight = slot.take(Time::min(), later(), never_stop);
    // Model a producer advancing through a full second (25 FPS) while the
    // consumer is busy. The in-flight surface must survive all replacements.
    for (int i = 1; i < 25; ++i) slot.publish(frame(i));
    require(Frame::alive == 2, "only in-flight and latest owners should remain");
    require(in_flight->id == 0, "replacement corrupted in-flight owner");
    slot.finish();
    auto newest = slot.take(Time::min(), later(), never_stop);
    require(newest && newest->id == 24, "slow inference replayed stale backlog");
    require(!slot.take(Time::min(), later(), never_stop), "EOF must finish after last pending frame");
    require(slot.counters().high_watermark == 1, "pending queue exceeded one frame");
    require(slot.counters().overwritten == 23, "overload drops mismatch");
    require(25 == 2 + slot.counters().drops(), "decoded/completed/drop accounting mismatch");
}

static void selection_after_rate_wait() {
    LatestSlot<Frame> slot;
    slot.publish(frame(1));
    const auto not_before = later(100);
    std::thread producer([&]() {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        slot.publish(frame(2));
        slot.finish();
    });
    auto selected = slot.take(not_before, later(), never_stop);
    producer.join();
    require(selected && selected->id == 2, "rate limiter reserved a frame before waiting");
    require(Clock::now() >= not_before, "rate cap admitted a frame too soon");
    require(slot.counters().overwritten == 1, "rate-limit replacement was not counted");
}

static void expiry_and_rate_math() {
    const auto origin = Time(std::chrono::seconds(10));
    require(!realtime::expired(origin, origin + std::chrono::milliseconds(250), 250), "age boundary discarded");
    require(realtime::expired(origin, origin + std::chrono::milliseconds(251), 250), "expired frame admitted");
    require(!realtime::expired(origin, origin + std::chrono::seconds(100), 0), "disabled age limit applied");
    require(realtime::next_inference(origin, 8) == origin + std::chrono::milliseconds(125), "8 FPS spacing wrong");
    require(realtime::next_inference(origin, 5) == origin + std::chrono::milliseconds(200), "5 FPS spacing wrong");
    require(realtime::next_inference(origin, 0) == origin, "unlimited rate delayed");
    require(realtime::next_inference(origin, 1e-300) == Time::max(), "tiny FPS overflowed the clock");
    // After a slow detection, the next period starts at its actual selection;
    // missed periods never accumulate inference debt.
    const auto delayed = origin + std::chrono::seconds(30);
    require(realtime::next_inference(delayed, 8) == delayed + std::chrono::milliseconds(125), "catch-up burst scheduled");
}

static void shutdown_and_stale_accounting() {
    LatestSlot<Frame> slot;
    slot.publish(frame(1));
    require(!slot.take(Time::min(), Clock::now(), never_stop), "deadline selected a frame");
    slot.discard_pending();
    slot.discard_stale(); // A decoded frame rejected before publish.
    slot.publish(frame(3));
    require(!slot.take(Time::min(), later(), []() { return true; }), "stop ignored");
    slot.finish();
    slot.discard_pending();
    slot.publish(frame(4)); // read() completing while shutdown is underway.
    const auto counts = slot.counters();
    require(counts.shutdown == 3 && counts.stale == 1 && counts.drops() == 4, "shutdown/stale counts mismatch");
    require(Frame::alive == 0, "shutdown retained a frame");
    require(!slot.take(Time::min(), later(), never_stop), "empty closed slot blocked");
}

static void concurrent_stress() {
    LatestSlot<Frame> slot;
    const int decoded = 10000;
    std::thread producer([&]() {
        for (int i = 0; i < decoded; ++i) {
            slot.publish(frame(i));
            if (i % 11 == 0) std::this_thread::yield();
        }
        slot.finish();
    });
    int previous = -1, completed = 0;
    bool ordered = true;
    while (auto current = slot.take(Time::min(), later(10000), never_stop)) {
        ordered = ordered && current->id > previous;
        previous = current->id;
        ++completed;
        if (completed % 7 == 0) std::this_thread::yield();
    }
    producer.join();
    require(ordered, "concurrent consumer received duplicate/reordered frames");
    require(previous == decoded - 1, "final decoded frame lost on EOF");
    require(completed + slot.counters().drops() == decoded, "concurrent count conservation failed");
    require(Frame::alive == 0, "concurrent slot leaked an owner");
}

int main() {
    try {
        ownership_and_overload();
        require(Frame::alive == 0, "overload owners leaked");
        selection_after_rate_wait();
        expiry_and_rate_math();
        shutdown_and_stale_accounting();
        concurrent_stress();
        std::cout << "PASS: latest ownership, overload, rate limiting, expiry, EOF, stop and concurrent accounting\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
