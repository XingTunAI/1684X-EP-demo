#ifndef HDMI_BOUNDED_METRICS_HPP
#define HDMI_BOUNDED_METRICS_HPP

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace hdmi_metrics {
constexpr std::size_t summary_sample_capacity = 4096;

// Capacity zero keeps every observation, preserving exact nearest-rank
// percentiles. A positive capacity uses deterministic Algorithm R sampling.
// Counts, sum and maximum always cover every observation, never just samples.
class Metric {
    std::size_t capacity_ = 0;
    uint64_t random_state_ = UINT64_C(0x653f2aeb18e7d049);
    std::vector<double> samples_;
    uint64_t random_word() {
        uint64_t value = (random_state_ += UINT64_C(0x9e3779b97f4a7c15));
        value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
        value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);
        return value ^ (value >> 31);
    }
    uint64_t random_below(uint64_t bound) {
        const uint64_t reject_below = -bound % bound;
        for (;;) {
            const auto value = random_word();
            if (value >= reject_below) return value % bound;
        }
    }
public:
    uint64_t count = 0;
    double sum = 0, maximum = 0;
    explicit Metric(std::size_t capacity = 0) { set_capacity(capacity); }
    void set_capacity(std::size_t capacity) {
        if (count) throw std::logic_error("Metric capacity must be set before observations");
        capacity_ = capacity;
        if (capacity_) samples_.reserve(capacity_);
    }
    void add(double value) {
        if (!std::isfinite(value)) throw std::invalid_argument("Metric requires finite observations");
        if (!count || value > maximum) maximum = value;
        ++count; sum += value;
        if (!capacity_ || samples_.size() < capacity_) samples_.push_back(value);
        else {
            const auto replacement = random_below(count);
            if (replacement < capacity_) samples_[static_cast<std::size_t>(replacement)] = value;
        }
    }
    std::size_t sample_capacity() const { return capacity_; }
    std::size_t sample_size() const { return samples_.size(); }
    bool approximate() const { return count > samples_.size(); }
    std::vector<double> sorted_samples() const {
        auto sorted = samples_; std::sort(sorted.begin(), sorted.end()); return sorted;
    }
    static double percentile(const std::vector<double>& sorted, double quantile) {
        if (sorted.empty() || !std::isfinite(quantile) || quantile <= 0 || quantile > 1)
            throw std::invalid_argument("Percentile requires samples and quantile in (0,1]");
        return sorted[static_cast<std::size_t>(std::ceil(quantile * sorted.size())) - 1];
    }
};

struct ContinuitySummary {
    uint64_t completed = 0;
    double seconds = 0, first_wait = 0, last_to_end = 0, max_adjacent = 0, max_including_edges = 0;
};

// Observe actual analysis completion times relative to formal measurement
// start. This deliberately has its own half-open population, independent of
// the later preview/optional-record accounting timestamp used for throughput.
class CompletionContinuity {
    uint64_t count_ = 0;
    double first_ = 0, last_ = 0, max_adjacent_ = 0;
public:
    void add(double since_measure_start, double requested_duration) {
        if (!std::isfinite(since_measure_start) || !std::isfinite(requested_duration) || requested_duration <= 0)
            throw std::invalid_argument("Invalid completion interval");
        if (since_measure_start < 0 || since_measure_start >= requested_duration) return;
        if (count_ && since_measure_start < last_) throw std::logic_error("Analysis completion times were reordered");
        if (!count_) first_ = since_measure_start;
        else max_adjacent_ = std::max(max_adjacent_, since_measure_start - last_);
        ++count_; last_ = since_measure_start;
    }
    ContinuitySummary summary(double measured_seconds) const {
        if (!std::isfinite(measured_seconds) || measured_seconds < 0 || (count_ && measured_seconds < last_))
            throw std::invalid_argument("Measurement end precedes a recorded completion");
        ContinuitySummary result;
        result.completed = count_; result.seconds = measured_seconds;
        if (!count_) { result.max_including_edges = measured_seconds; return result; }
        result.first_wait = first_; result.last_to_end = measured_seconds - last_;
        result.max_adjacent = max_adjacent_;
        result.max_including_edges = std::max(std::max(result.first_wait, result.last_to_end), result.max_adjacent);
        return result;
    }
};

// Error and stop paths can leave an in-flight decode without a completion.
// Preserve that evidence; never wrap unsigned subtraction or invent a drop.
inline bool accounting_complete(uint64_t decoded, uint64_t completed, uint64_t drops, bool error_free) {
    return error_free && completed <= decoded && drops == decoded - completed;
}
} // namespace hdmi_metrics
#endif
