#ifndef HDMI_DECODE_OBSERVATION_HPP
#define HDMI_DECODE_OBSERVATION_HPP

#include <array>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace decode_observation {
// Twenty completed 100 ms buckets plus the current bucket. Excluding the
// partial current bucket avoids systematically under-reporting paced input.
class RollingRate {
    struct Bucket { int64_t tick = -1; uint64_t count = 0; };
    std::array<Bucket, 21> buckets_;
public:
    static int64_t tick(double seconds) { return static_cast<int64_t>(std::floor(seconds * 10)); }
    void add(double seconds) {
        const auto index = tick(seconds);
        auto& bucket = buckets_[static_cast<std::size_t>((index % 21 + 21) % 21)];
        if (bucket.tick != index) { bucket.tick = index; bucket.count = 0; }
        ++bucket.count;
    }
    double fps(double now, double start) const {
        const auto boundary = tick(now);
        const double elapsed = std::min(2.0, std::max(0.0, boundary / 10.0 - start));
        if (elapsed <= 0) return 0;
        uint64_t count = 0;
        for (const auto& bucket : buckets_)
            if (bucket.tick < boundary && bucket.tick >= boundary - 20) count += bucket.count;
        return count / elapsed;
    }
};

struct StreamSnapshot {
    uint64_t decoded = 0, inferred = 0, decode_sequence = 0, infer_sequence = 0;
    double target_fps = -1, decode_fps = 0, infer_fps = 0;
    double decode_source_seconds = -1, infer_source_seconds = -1;
    double last_decode_time = -1, last_infer_time = -1;
    double last_decoded_age_ms = -1, last_inferred_age_ms = -1;
    double source_lag_ms = -1, read_ms = -1, slow_seconds = 0;
    bool live = false, has_decode = false, has_inference = false;
    bool warming = true, stalled = false, sustained_slow = false;
};

class Ledger {
    struct Stream {
        StreamSnapshot data;
        RollingRate decode_rate, infer_rate;
        double slow_since = -1;
    };
    std::vector<Stream> streams_;
    bool started_ = false;
    double start_ = 0;
    static double nonnegative(double value) { return std::isfinite(value) && value >= 0 ? value : -1; }
    Stream& stream(std::size_t id) { return streams_.at(id); }
public:
    explicit Ledger(std::size_t count) : streams_(count) {}
    void configure_source(std::size_t id, double source_fps, bool live) {
        auto& data = stream(id).data;
        data.live = live;
        data.target_fps = !live && std::isfinite(source_fps) && source_fps > 0 ? source_fps : -1;
    }
    void start(double now) {
        if (started_ || !std::isfinite(now)) throw std::logic_error("Observation clock must start once");
        start_ = now; started_ = true;
        for (auto& item : streams_) { item.decode_rate = RollingRate(); item.infer_rate = RollingRate(); item.slow_since = -1; }
    }
    void decoded(std::size_t id, uint64_t sequence, double source_seconds,
                 double when, double lag_ms, double read_ms) {
        if (!std::isfinite(when)) throw std::invalid_argument("Invalid decode observation time");
        auto& item = stream(id);
        auto& data = item.data;
        ++data.decoded; data.has_decode = true; data.decode_sequence = sequence;
        data.decode_source_seconds = nonnegative(source_seconds); data.last_decode_time = when;
        data.source_lag_ms = data.live ? -1 : nonnegative(lag_ms); data.read_ms = nonnegative(read_ms);
        // Primed frames retain their real pre-clock timestamp and identity;
        // they must not create invented events in the rolling observation window.
        if (started_ && when >= start_) item.decode_rate.add(when);
    }
    void inferred(std::size_t id, uint64_t sequence, double source_seconds, double when) {
        if (!std::isfinite(when)) throw std::invalid_argument("Invalid inference observation time");
        auto& item = stream(id);
        auto& data = item.data;
        ++data.inferred; data.has_inference = true; data.infer_sequence = sequence;
        data.infer_source_seconds = nonnegative(source_seconds); data.last_infer_time = when;
        if (started_ && when >= start_) item.infer_rate.add(when);
    }
    std::vector<StreamSnapshot> snapshot(double now) {
        if (!std::isfinite(now)) throw std::invalid_argument("Invalid observation snapshot time");
        std::vector<StreamSnapshot> result;
        result.reserve(streams_.size());
        for (auto& item : streams_) {
            auto data = item.data;
            data.warming = !started_ || now - start_ < 2;
            if (started_) { data.decode_fps = item.decode_rate.fps(now, start_); data.infer_fps = item.infer_rate.fps(now, start_); }
            if (data.has_decode) data.last_decoded_age_ms = std::max(0.0, now - data.last_decode_time) * 1000;
            if (data.has_inference) data.last_inferred_age_ms = std::max(0.0, now - data.last_infer_time) * 1000;
            data.stalled = !data.warming && (!data.has_decode || data.last_decoded_age_ms > 2000);
            if (!data.warming && data.target_fps > 0 && data.decode_fps < data.target_fps * .85) {
                if (item.slow_since < 0) item.slow_since = now;
                data.slow_seconds = std::max(0.0, now - item.slow_since);
                data.sustained_slow = data.slow_seconds >= 3;
            } else item.slow_since = -1;
            result.push_back(data);
        }
        return result;
    }
};
} // namespace decode_observation
#endif
