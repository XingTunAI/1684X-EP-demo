import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_dispatch_wait import (
    analyze_directory, analyze_graph, analyze_sessions, negative_paths,
)


def sample(time, count=None):
    count = int(time * 100) if count is None else count
    return {"time": time, "line": int(time * 2 + 1),
            "counters": {"inference_frames": count, "decoded_frames": count * 2},
            "stream_inference_counts": {"0": count}}


def capture(start=20, end=22):
    return {"roots": ["bm1686_trigger_vpp"], "before_enable_monotonic": start,
            "after_enable_monotonic": start + .001,
            "before_disable_monotonic": end - .001,
            "after_disable_monotonic": end}


class DispatchWaitTests(unittest.TestCase):
    def test_negative_deep_child_excludes_whole_root(self):
        trace = "\n".join([
            "1.000000 | 0) worker-5 | |  bm1686_trigger_vpp() {",
            "1.000001 | 0) worker-5 | |    bmdev_memcpy_s2d_internal() {",
            "1.000002 | 0) worker-5 | |      bm1684_cdma_transfer() {",
            "1.000003 | 0) worker-5 | 30 us |        mutex_lock();",
            "1.000012 | 0) worker-5 | 10 us |      }",
            "1.000040 | 0) worker-5 | 40 us |    }",
            "1.000100 | 0) worker-5 | 100 us |  }",
            "2.000000 | 0) worker-5 | 50 us |  bm1686_trigger_vpp();",
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vpp.trace"
            path.write_text(trace, encoding="utf-8")
            result, detail = analyze_graph(path, capture())
        root = result["summary_after_filter"]["bm1686_trigger_vpp"]
        self.assertEqual(root["observed_entry_records"], 2)
        self.assertEqual(root["parsed_complete_roots"], 2)
        self.assertEqual(root["accepted_roots"], 1)
        self.assertEqual(root["stages"]["inclusive_us"]["mean_us"], 50)
        self.assertEqual(len(detail["original_parse"]["calls"]), 2)
        self.assertEqual(result["excluded_negative_roots"][0]["reasons"][0]["remainder_us"], -20)

    def test_session_brackets_entire_sampling_gap_and_guard_excludes_neighbors(self):
        samples = [sample(i / 2) for i in range(80) if not 20 <= i / 2 < 24]
        result = analyze_sessions(samples, {"vpp": capture()}, {
            "measurement_start_monotonic_s": 0, "measurement_end_monotonic_s": 40,
        })
        session = result["sessions"]["vpp"]
        self.assertEqual(session["counter_session"]["start"], 19.5)
        self.assertEqual(session["counter_session"]["end"], 24)
        self.assertEqual(session["counter_session"]["seconds"], 4.5)
        self.assertEqual(session["counter_session"]["rates_per_second"]["inference_frames"], 100)
        self.assertEqual(session["relative_session_rate_change_pct"]["inference_frames"], 0)
        self.assertTrue(session["both_baselines_long_enough"])
        self.assertEqual(result["exclusion_intervals_including_guard"], [(17.5, 26)])
        self.assertTrue(all(row["end"] <= 17.5 or row["start"] >= 26
                            for row in result["eligible_untraced_intervals"]))

    def test_counter_reset_is_not_negative_throughput(self):
        samples = [sample(1, 100), sample(2, 200), sample(3, 0), sample(4, 100)]
        result = analyze_sessions(samples, {}, {
            "measurement_start_monotonic_s": 0, "measurement_end_monotonic_s": 5,
        })
        self.assertEqual(len(result["invalid_counter_intervals_sample_lines"]), 1)
        self.assertEqual(result["untraced_baseline"]["covered_seconds"], 2)

    def test_directory_preserves_source_and_allows_smoke_without_status(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source", Path(directory) / "analysis"
            source.mkdir()
            (source / "vpp.capture.json").write_text(json.dumps(capture()), encoding="utf-8")
            content = "20.000000 | 0) worker-5 | 50 us |  bm1686_trigger_vpp();\n"
            (source / "vpp.trace").write_text(content, encoding="utf-8")
            result = analyze_directory(source, output)
            self.assertFalse(result["stages"]["source"]["throughput"]["available"])
            self.assertEqual((source / "vpp.trace").read_text(encoding="utf-8"), content)
            self.assertTrue((output / "report.md").exists())
            self.assertTrue((output / "stages.csv").exists())
            with self.assertRaises(FileExistsError):
                analyze_directory(source, output)


if __name__ == "__main__":
    unittest.main()
