#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace score_gate {
constexpr int rows = 8400;
constexpr int stride = 84;
constexpr int classes = 80;
constexpr std::size_t full_bytes = rows * stride * sizeof(float);
constexpr std::size_t score_bytes = rows * sizeof(float);
// Conservative experiment limits; do not discard candidates when exceeded.
constexpr std::size_t max_ranges = 64;
constexpr std::size_t max_rows = 8000;
constexpr unsigned byte_fraction_percent = 80;
struct Plan {
    std::vector<std::pair<int, int>> ranges; // Original candidate ranges: first row, number of rows.
    std::vector<std::pair<int, int>> read_ranges; // May also cover non-candidate rows between ranges.
    std::size_t selected_rows = 0, selected_bytes = 0;
    std::size_t copied_rows = 0, copied_bytes = 0;
    std::string fallback;
};
inline Plan make_plan(const std::vector<float>& maxima, float threshold, std::size_t extra_byte_budget = 0) {
    if (maxima.size() != rows || !std::isfinite(threshold) || threshold <= 0)
        throw std::runtime_error("Unsupported score gate rows or threshold");
    Plan result;
    bool nonfinite = false;
    for (int row = 0; row < rows; ++row) {
        if (!std::isfinite(maxima[row])) { nonfinite = true; continue; }
        if (!(maxima[row] > threshold)) continue;
        ++result.selected_rows;
        if (!result.ranges.empty() && result.ranges.back().first + result.ranges.back().second == row)
            ++result.ranges.back().second;
        else result.ranges.emplace_back(row, 1);
    }
    result.selected_bytes = result.selected_rows * stride * sizeof(float);
    result.copied_rows = result.selected_rows;
    // Bridging each gap saves one DMA call. Choose the cheapest gaps first;
    // ties use the original range index so the same frame always has one plan.
    std::vector<bool> bridge(result.ranges.empty() ? 0 : result.ranges.size() - 1, false);
    if (extra_byte_budget && result.ranges.size() > 1) {
        std::vector<std::pair<std::size_t, std::size_t>> gaps;
        gaps.reserve(result.ranges.size() - 1);
        for (std::size_t i = 1; i < result.ranges.size(); ++i) {
            const auto& previous = result.ranges[i - 1];
            const auto gap_rows = static_cast<std::size_t>(result.ranges[i].first - previous.first - previous.second);
            gaps.emplace_back(gap_rows, i - 1);
        }
        std::sort(gaps.begin(), gaps.end());
        for (const auto& gap : gaps) {
            const auto extra_bytes = gap.first * stride * sizeof(float);
            if (extra_bytes > extra_byte_budget) break;
            extra_byte_budget -= extra_bytes;
            result.copied_rows += gap.first;
            bridge[gap.second] = true;
        }
    }
    for (std::size_t i = 0; i < result.ranges.size(); ++i) {
        const auto& range = result.ranges[i];
        if (i && bridge[i - 1])
            result.read_ranges.back().second = range.first + range.second - result.read_ranges.back().first;
        else result.read_ranges.push_back(range);
    }
    result.copied_bytes = result.copied_rows * stride * sizeof(float);
    if (nonfinite) result.fallback = "nonfinite_maximum";
    else if (result.selected_rows > max_rows) result.fallback = "too_many_rows";
    else if (result.ranges.size() > max_ranges) result.fallback = "too_many_ranges";
    else if ((result.copied_bytes + score_bytes) * 100 >= full_bytes * byte_fraction_percent)
        result.fallback = "bytes_near_full";
    return result;
}

// Call only after every read_range was copied successfully. Validate and
// publish only original candidates; bytes copied solely to bridge a gap must
// never introduce a candidate, a nonfinite fallback, or a stale sparse row.
// A null sparse_rows selects dense mode, whose unselected output was zeroed
// before the reads. Restore the zeroes overwritten by bridged gap data.
inline bool prepare_host_candidates(const Plan& plan, float* host, std::vector<int>* sparse_rows) {
    if (sparse_rows) sparse_rows->clear();
    for (const auto& range : plan.ranges) {
        const auto first = range.first * stride;
        const auto count = range.second * stride;
        if (std::any_of(host + first, host + first + count, [](float value) { return !std::isfinite(value); }))
            return false;
    }
    if (sparse_rows) {
        for (const auto& range : plan.ranges)
            for (int row = range.first; row < range.first + range.second; ++row)
                sparse_rows->push_back(row);
    } else {
        std::size_t candidate = 0;
        for (const auto& read : plan.read_ranges) {
            int cursor = read.first;
            const int end = read.first + read.second;
            while (candidate < plan.ranges.size() && plan.ranges[candidate].first < end) {
                const auto& range = plan.ranges[candidate++];
                std::fill(host + cursor * stride, host + range.first * stride, 0.0f);
                cursor = range.first + range.second;
            }
            std::fill(host + cursor * stride, host + end * stride, 0.0f);
        }
    }
    return true;
}
}
