#include "../bounded_metrics.hpp"
#include <iostream>
#include <limits>
#include <stdexcept>

static void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
using hdmi_metrics::Metric;
using hdmi_metrics::CompletionContinuity;

static void full_mode_exact_percentiles() {
    Metric full;
    for (int i = 10000; i >= 1; --i) full.add(i);
    require(full.count == 10000 && full.sample_size() == 10000 && !full.sample_capacity() && !full.approximate(),
            "full mode discarded observations or reports approximate quantiles");
    require(full.sum == 50005000 && full.maximum == 10000, "full exact aggregate changed");
    const auto sorted = full.sorted_samples();
    require(Metric::percentile(sorted, .5) == 5000 && Metric::percentile(sorted, .95) == 9500 &&
            Metric::percentile(sorted, .99) == 9900 && Metric::percentile(sorted, 1) == 10000,
            "full nearest-rank percentile semantics changed");
    require(full.sample_size() == 10000, "reading quantiles mutated observations");
}

static void bounded_long_run_and_exact_aggregates() {
    Metric bounded(hdmi_metrics::summary_sample_capacity), same_run(hdmi_metrics::summary_sample_capacity);
    constexpr uint64_t observations = 1000000;
    for (uint64_t i = 1; i <= observations; ++i) {
        bounded.add(static_cast<double>(i)); same_run.add(static_cast<double>(i));
        if (i % 10000 == 0) require(bounded.sample_size() == hdmi_metrics::summary_sample_capacity,
                                  "long-run percentile storage grew beyond its fixed capacity");
    }
    require(bounded.count == observations && bounded.sum == 500000500000.0 && bounded.maximum == observations,
            "summary aggregate used reservoir observations instead of all frames");
    require(bounded.approximate() && bounded.sample_capacity() == hdmi_metrics::summary_sample_capacity,
            "reservoir truncation was not marked approximate");
    const auto samples = bounded.sorted_samples();
    require(samples == same_run.sorted_samples(), "reservoir is not deterministic for the same inputs");
    // A deterministic broad distribution check catches retaining only an early
    // prefix or a recent tail; this is not a promised quantile error bound.
    const double median = Metric::percentile(samples, .5), p95 = Metric::percentile(samples, .95);
    require(median > 450000 && median < 550000 && p95 > 900000 && p95 < 990000,
            "reservoir no longer represents the full observation interval");
    require(samples.front() >= 1 && samples.back() <= observations, "reservoir contains invented values");
}

static void small_samples_negative_values_and_invalid_input() {
    Metric empty(4);
    require(!empty.count && !empty.sample_size() && empty.sum == 0 && !empty.approximate(), "empty metric fabricated observations");
    bool rejected = false;
    try { Metric::percentile(empty.sorted_samples(), .5); }
    catch (const std::invalid_argument&) { rejected = true; }
    require(rejected, "empty metric fabricated a percentile");
    for (double value : {-4.0, -3.0, -2.0, -1.0}) empty.add(value);
    require(empty.count == 4 && empty.sum == -10 && empty.maximum == -1 && !empty.approximate(),
            "capacity boundary or negative maximum is wrong");
    empty.add(-9);
    require(empty.count == 5 && empty.sum == -19 && empty.maximum == -1 && empty.approximate() && empty.sample_size() == 4,
            "first reservoir replacement changed exact aggregates");
    rejected = false;
    try { empty.set_capacity(0); }
    catch (const std::logic_error&) { rejected = true; }
    require(rejected, "metric changed sampling policy after observations");
    for (double invalid : {std::numeric_limits<double>::infinity(), std::numeric_limits<double>::quiet_NaN()}) {
        rejected = false;
        try { empty.add(invalid); }
        catch (const std::invalid_argument&) { rejected = true; }
        require(rejected && empty.count == 5 && empty.sum == -19, "invalid observation poisoned exact aggregates");
    }
    Metric one(1);
    one.add(1000000000);
    for (int i = 0; i < 1000; ++i) one.add(1);
    require(one.sample_size() == 1 && one.maximum == 1000000000 && one.sum == 1000001000 && one.count == 1001,
            "a discarded peak was lost from the exact maximum");
}

static void continuity_half_open_boundary_and_middle_gap() {
    CompletionContinuity continuity;
    for (double t : {-1.0, -.0001, 0.0, 1.0, 1.0, 5.0, 9.5, 10.0, 11.0}) continuity.add(t, 10);
    const auto s = continuity.summary(10);
    require(s.completed == 5 && s.first_wait == 0 && s.last_to_end == .5,
            "formal completion interval must include start and exclude end");
    require(s.max_adjacent == 4.5 && s.max_including_edges == 4.5, "middle completion gap disappeared");
    bool rejected = false;
    try { continuity.add(3, 10); }
    catch (const std::logic_error&) { rejected = true; }
    require(rejected && continuity.summary(10).completed == 5, "reordered completion corrupted continuity");
}

static void continuity_empty_startup_and_early_stop() {
    CompletionContinuity empty;
    empty.add(-.25, 10); // A warmup result does not eliminate formal first-frame waiting.
    const auto no_interval = empty.summary(0), no_result = empty.summary(3);
    require(no_interval.completed == 0 && no_interval.max_including_edges == 0, "stop before formal start fabricated a result");
    require(no_result.completed == 0 && no_result.seconds == 3 && no_result.max_including_edges == 3,
            "empty channel must retain its whole observed waiting interval");
    CompletionContinuity single;
    single.add(2, 10);
    const auto early = single.summary(3), full = single.summary(10);
    require(early.completed == 1 && early.first_wait == 2 && early.last_to_end == 1 && early.max_including_edges == 2,
            "early stop used the requested end instead of the observed end");
    require(full.last_to_end == 8 && full.max_including_edges == 8 && full.max_adjacent == 0,
            "single result lost its tail gap or invented an adjacent pair");
    bool rejected = false;
    try { single.summary(1); }
    catch (const std::invalid_argument&) { rejected = true; }
    require(rejected, "summary ended before an observed completion");
    // An inference completing before the deadline can have preview/record
    // accounting after it. Continuity tracks the former independently.
    CompletionContinuity crossing;
    crossing.add(9.9, 10);
    require(crossing.summary(10).completed == 1, "late record accounting erased an in-interval analysis completion");
}

static void all_latest_stop_and_error_accounting() {
    using hdmi_metrics::accounting_complete;
    require(accounting_complete(0, 0, 0, true), "empty accounting is not balanced");
    require(accounting_complete(7, 7, 0, true), "all/EOF-stop completed frames no longer balance");
    require(accounting_complete(7, 4, 3, true), "latest overwrite/stale/shutdown counts no longer balance");
    require(!accounting_complete(7, 6, 0, true), "stop with in-flight work invented a drop/completion");
    require(!accounting_complete(7, 7, 0, false), "balanced counts concealed a stream error");
    require(!accounting_complete(7, 8, 0, true) && !accounting_complete(7, 6, 2, true), "over-counting was accepted");
    const auto maximum = std::numeric_limits<uint64_t>::max();
    require(accounting_complete(maximum, maximum, 0, true) && !accounting_complete(0, maximum, 1, true),
            "unsigned overflow fabricated complete accounting");
}

int main() {
    try {
        full_mode_exact_percentiles();
        bounded_long_run_and_exact_aggregates();
        small_samples_negative_values_and_invalid_input();
        continuity_half_open_boundary_and_middle_gap();
        continuity_empty_startup_and_early_stop();
        all_latest_stop_and_error_accounting();
        std::cout << "PASS: full exact metrics, bounded million-observation reservoir, continuity boundaries, stop and error accounting\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
