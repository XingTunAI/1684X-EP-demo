#pragma once
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
    std::vector<std::pair<int, int>> ranges; // first row, number of rows
    std::size_t selected_rows = 0, selected_bytes = 0;
    std::string fallback;
};
inline Plan make_plan(const std::vector<float>& maxima, float threshold) {
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
    if (nonfinite) result.fallback = "nonfinite_maximum";
    else if (result.selected_rows > max_rows) result.fallback = "too_many_rows";
    else if (result.ranges.size() > max_ranges) result.fallback = "too_many_ranges";
    else if ((result.selected_bytes + score_bytes) * 100 >= full_bytes * byte_fraction_percent)
        result.fallback = "bytes_near_full";
    return result;
}
}
