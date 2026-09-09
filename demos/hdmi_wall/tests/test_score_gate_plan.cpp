#include "../detector/score_gate_plan.hpp"
#include <iostream>
#include <limits>
#include <stdexcept>

using score_gate::Plan;
using Ranges = std::vector<std::pair<int, int>>;
constexpr std::size_t row_bytes = score_gate::stride * sizeof(float);

static void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

static std::vector<float> maxima(std::initializer_list<int> candidates) {
    std::vector<float> result(score_gate::rows, 0.0f);
    for (int row : candidates) result[row] = .75f;
    return result;
}

static void validation_and_empty() {
    for (float threshold : {0.0f, -.1f, std::numeric_limits<float>::infinity(),
                            std::numeric_limits<float>::quiet_NaN()}) {
        bool threw = false;
        try { score_gate::make_plan(maxima({}), threshold); }
        catch (const std::runtime_error&) { threw = true; }
        require(threw, "invalid confidence accepted");
    }
    bool threw = false;
    try { score_gate::make_plan(std::vector<float>(12, 0.0f), .25f); }
    catch (const std::runtime_error&) { threw = true; }
    require(threw, "invalid output shape accepted");
    auto values = maxima({});
    values[2] = .25f; // Equality must stay excluded, as in CPU confidence filtering.
    values[3] = -.5f;
    for (std::size_t budget : {std::size_t(0), std::size_t(1024 * 1024)}) {
        const auto plan = score_gate::make_plan(values, .25f, budget);
        require(plan.ranges.empty() && plan.read_ranges.empty() && plan.fallback.empty(), "empty selection must read no rows");
        require(!plan.selected_rows && !plan.selected_bytes && !plan.copied_rows && !plan.copied_bytes, "empty byte accounting");
        std::vector<int> rows{12, 42};
        require(score_gate::prepare_host_candidates(plan, nullptr, &rows) && rows.empty(), "empty sparse frame retained stale candidates");
        require(score_gate::prepare_host_candidates(plan, nullptr, nullptr), "empty dense selection dereferenced host data");
    }
}

static void zero_budget_preserves_original_ranges() {
    const auto values = maxima({0, 1, 5, 9, 10, 8399});
    const auto original = score_gate::make_plan(values, .25f);
    const auto explicit_zero = score_gate::make_plan(values, .25f, 0);
    const Ranges expected{{0, 2}, {5, 1}, {9, 2}, {8399, 1}};
    require(original.ranges == expected && original.read_ranges == expected, "budget zero changed adjacent-only DMA ranges");
    require(explicit_zero.ranges == original.ranges && explicit_zero.read_ranges == original.read_ranges, "default budget differs from zero");
    require(original.selected_rows == 6 && original.copied_rows == 6, "zero budget row counts");
    require(original.selected_bytes == 6 * row_bytes && original.copied_bytes == original.selected_bytes, "zero budget copies gap bytes");
}

static void cheapest_gaps_and_stable_ties() {
    const auto values = maxima({1, 11, 14, 16}); // Gaps cost 9, 2 and 1 rows.
    const auto plan = score_gate::make_plan(values, .25f, 3 * row_bytes);
    require(plan.ranges == Ranges({{1, 1}, {11, 1}, {14, 1}, {16, 1}}), "merge rewrote original candidates");
    require(plan.read_ranges == Ranges({{1, 1}, {11, 6}}), "greedy merger did not pick cheapest gaps globally");
    require(plan.selected_rows == 4 && plan.copied_rows == 7 && plan.copied_bytes - plan.selected_bytes == 3 * row_bytes,
            "merged payload accounting wrong");
    const auto partial = score_gate::make_plan(values, .25f, 2 * row_bytes);
    require(partial.read_ranges == Ranges({{1, 1}, {11, 1}, {14, 3}}), "unaffordable gap consumed budget");
    const auto ties = score_gate::make_plan(maxima({1, 4, 7, 10}), .25f, 4 * row_bytes);
    require(ties.read_ranges == Ranges({{1, 7}, {10, 1}}), "equal-cost gaps must prefer original ascending index");
    const auto all = score_gate::make_plan(values, .25f, std::numeric_limits<std::size_t>::max());
    require(all.read_ranges == Ranges({{1, 16}}) && all.copied_rows == 16, "large budget overflowed or expanded beyond bounding range");
}

static void exact_budget_and_expanded_byte_fallback() {
    const auto values = maxima({2, 6});
    const auto below = score_gate::make_plan(values, .25f, 3 * row_bytes - 1);
    const auto exact = score_gate::make_plan(values, .25f, 3 * row_bytes);
    require(below.read_ranges.size() == 2 && below.copied_rows == 2, "sub-byte boundary overspent budget");
    require(exact.read_ranges == Ranges({{2, 5}}) && exact.copied_rows == 5, "exact budget boundary did not merge");

    auto dense = maxima({6619});
    std::fill(dense.begin(), dense.begin() + 6599, .75f); // 6600 selected; bridging 20 gives 6620 copied.
    const auto original = score_gate::make_plan(dense, .25f);
    const auto expanded = score_gate::make_plan(dense, .25f, 20 * row_bytes);
    require(original.fallback.empty(), "unexpanded payload unexpectedly near full");
    require(expanded.selected_rows == 6600 && expanded.copied_rows == 6620, "expanded full-byte boundary rows wrong");
    require(expanded.fallback == "bytes_near_full", "80% fallback ignored actual expanded transfer bytes");
    dense[6619] = 0.0f;
    dense[6618] = .75f;
    const auto just_below = score_gate::make_plan(dense, .25f, 19 * row_bytes);
    require(just_below.copied_rows == 6619 && just_below.fallback.empty(), "one row below 80% incorrectly fell back");
}

static void original_fallback_guards() {
    auto many_ranges = maxima({});
    for (int i = 0; i < 65; ++i) many_ranges[i * 2] = .75f;
    const auto merged = score_gate::make_plan(many_ranges, .25f, 1024 * 1024);
    require(merged.read_ranges.size() == 1 && merged.ranges.size() == 65 && merged.fallback == "too_many_ranges",
            "merge relaxed original candidate-range fallback");
    auto many_rows = maxima({});
    std::fill(many_rows.begin(), many_rows.begin() + 8001, .75f);
    require(score_gate::make_plan(many_rows, .25f).fallback == "too_many_rows", "original selected-row limit changed");
    for (float invalid : {std::numeric_limits<float>::quiet_NaN(), std::numeric_limits<float>::infinity(),
                          -std::numeric_limits<float>::infinity()}) {
        many_ranges[7000] = invalid;
        require(score_gate::make_plan(many_ranges, .25f, 1024 * 1024).fallback == "nonfinite_maximum",
                "nonfinite maximum lost original fallback priority");
    }
}

static std::vector<float> copy_ranges(const Plan& plan, const std::vector<float>& device, float initial = 0.0f) {
    std::vector<float> host(score_gate::rows * score_gate::stride, initial);
    for (const auto& range : plan.read_ranges) {
        const int first = range.first * score_gate::stride;
        const int end = (range.first + range.second) * score_gate::stride;
        std::copy(device.begin() + first, device.begin() + end, host.begin() + first);
    }
    return host;
}

static void host_candidate_identity_and_dense_gap_masking() {
    const auto values = maxima({1, 2, 5, 10, 11, 15});
    const auto baseline = score_gate::make_plan(values, .25f);
    const auto merged = score_gate::make_plan(values, .25f, 100 * row_bytes);
    std::vector<float> device(score_gate::rows * score_gate::stride);
    for (std::size_t i = 0; i < device.size(); ++i) device[i] = static_cast<float>((i % 971) + 1);
    // Gap contents are deliberately hostile. They must neither enter CPU
    // candidates nor introduce a fallback, regardless of transport grouping.
    device[3 * score_gate::stride] = std::numeric_limits<float>::quiet_NaN();
    device[6 * score_gate::stride + 4] = std::numeric_limits<float>::infinity();
    auto expected = copy_ranges(baseline, device);
    auto dense = copy_ranges(merged, device);
    require(score_gate::prepare_host_candidates(merged, dense.data(), nullptr), "nonfinite gap triggered candidate fallback");
    require(dense == expected, "merged dense output differs from original selected-row copies");

    auto sparse = copy_ranges(merged, device, std::numeric_limits<float>::quiet_NaN());
    std::vector<int> selected{7000};
    require(score_gate::prepare_host_candidates(merged, sparse.data(), &selected), "undefined sparse non-candidate memory was scanned");
    require(selected == std::vector<int>({1, 2, 5, 10, 11, 15}), "candidate order/identity changed after merging");
    for (int row : selected)
        for (int column = 0; column < score_gate::stride; ++column)
            require(sparse[row * score_gate::stride + column] == device[row * score_gate::stride + column], "candidate pixels changed");

    // Validate all selected rows before publishing any sparse indices.
    sparse[15 * score_gate::stride + 83] = std::numeric_limits<float>::quiet_NaN();
    require(!score_gate::prepare_host_candidates(merged, sparse.data(), &selected) && selected.empty(),
            "nonfinite late candidate published a partial/stale sparse view");
    dense[5 * score_gate::stride] = std::numeric_limits<float>::infinity();
    require(!score_gate::prepare_host_candidates(merged, dense.data(), nullptr), "nonfinite selected box geometry escaped dense fallback");
}

static void separated_read_ranges_mask_only_bridged_rows() {
    const auto values = maxima({0, 3, 20, 22, 8399});
    const auto original = score_gate::make_plan(values, .25f);
    const auto merged = score_gate::make_plan(values, .25f, 3 * row_bytes);
    require(merged.read_ranges == Ranges({{0, 4}, {20, 3}, {8399, 1}}), "separated groups merged across an unaffordable gap");
    std::vector<float> device(score_gate::rows * score_gate::stride, .8f);
    auto host = copy_ranges(merged, device);
    require(score_gate::prepare_host_candidates(merged, host.data(), nullptr), "finite separated ranges rejected");
    require(host == copy_ranges(original, device), "dense masking crossed range or tensor boundary");
}

int main() {
    try {
        validation_and_empty();
        zero_budget_preserves_original_ranges();
        cheapest_gaps_and_stable_ties();
        exact_budget_and_expanded_byte_fallback();
        original_fallback_guards();
        host_candidate_identity_and_dense_gap_masking();
        separated_read_ranges_mask_only_bridged_rows();
        std::cout << "PASS: score gate merge budgets, ordering, payload limits, fallbacks, sparse identity and dense gap masking\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
