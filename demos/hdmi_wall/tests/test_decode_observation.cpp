#include "../decode_observation.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

static void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
int main() {
    try {
        decode_observation::Ledger ledger(3);
        ledger.configure_source(0, 24, false);
        ledger.configure_source(1, std::numeric_limits<double>::quiet_NaN(), false);
        ledger.configure_source(2, 24, true);
        ledger.start(10);
        ledger.decoded(0, 0, 0, 9, 0, 100); // Actual priming before source clock.
        auto rows = ledger.snapshot(10.1);
        require(rows[0].decoded == 1 && rows[0].decode_fps == 0 && rows[0].decode_sequence == 0,
                "pre-clock decode fabricated a rolling event or lost identity");
        for (int i = 0; i < 48; ++i) {
            const double t = 10.01 + i / 24.0;
            ledger.decoded(0, i + 1, i / 24.0, t, 12, 4);
            ledger.decoded(1, i, i / 24.0, t, 5, 2);
            if (i % 3 == 0) ledger.inferred(0, i + 1, i / 24.0, t + .001);
        }
        rows = ledger.snapshot(11.99);
        require(rows[0].decode_fps > 23 && rows[0].decode_fps < 25 && rows[0].infer_fps > 7 && rows[0].infer_fps < 9,
                "decode and inference rates no longer count independent real events");
        require(rows[0].decoded == 49 && rows[0].inferred == 16 && rows[0].source_lag_ms == 12,
                "all-decode counters were derived from inference or lost lag");
        rows = ledger.snapshot(14.1);
        require(rows[0].decode_fps == 0 && rows[0].infer_fps == 0 && rows[0].stalled,
                "stopped input left a frozen nonzero FPS");
        rows = ledger.snapshot(17.2);
        require(rows[0].sustained_slow && !rows[1].sustained_slow && !rows[2].sustained_slow,
                "sustained slow missing or invented a target for unknown/live source");
        require(rows[1].target_fps < 0 && rows[2].target_fps < 0 && rows[2].stalled,
                "live source metadata became a guaranteed target or masked missing input");
        for (int i = 0; i < 48; ++i) ledger.decoded(0, 100 + i, 10 + i / 24.0, 18.01 + i / 24.0, 2, 1);
        rows = ledger.snapshot(19.99);
        require(!rows[0].sustained_slow && !rows[0].stalled && rows[0].decode_sequence == 147,
                "recovery did not clear slow state or preserve latest identity");
        // Long/high-rate input must not grow an event history, lose total
        // counters, or make the rolling rate depend on inference activity.
        decode_observation::Ledger long_run(1);
        long_run.configure_source(0, 1000, false); long_run.start(0);
        for (int i = 0; i < 1000000; ++i) long_run.decoded(0, i, i / 1000.0, i / 1000.0, 0, .1);
        rows = long_run.snapshot(999.999);
        require(rows[0].decoded == 1000000 && rows[0].decode_fps > 949 && rows[0].decode_fps <= 1001 && rows[0].infer_fps == 0,
                "bounded high-rate observation lost events or coupled inference");
        require(long_run.snapshot(1003)[0].decode_fps == 0, "old ring buckets did not expire");
        decode_observation::RollingRate paced;
        for (int i = 0; i < 240; ++i) {
            const double when = i / 24.0 + .0001;
            paced.add(when);
            if (when > 2.1) require(std::abs(paced.fps(when, 0) - 24) < .01,
                                   "partial bucket biased a constant 24 FPS input downward");
        }
        std::cout << "decode observation tests passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
