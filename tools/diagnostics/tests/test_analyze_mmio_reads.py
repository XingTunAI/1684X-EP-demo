import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_mmio_reads import cpu_members, stats, trace_modes
from analyze_dispatch_graph import parse_trace


def graph(n=3, pid=42):
    lines = []
    for index in range(n):
        start = 1.1 + index * 0.01
        prefix = lambda t: f"{t:.6f} | 6) probe-{pid} | "
        lines.extend([prefix(start) + " |  bm_get_reg() {",
                      prefix(start + .000001) + "5 us |    bm_read32();",
                      prefix(start + .000006) + "6 us |  } /* bm_get_reg */"])
    return parse_trace("\n".join(lines), roots=["bm_get_reg"])


class MmioAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.probe = {"pid": 42, "success": True, "runs": [
            {"mode": "continuous", "samples": [[99], [100]],
             "complete": True, "timer_value_changed": True, "expected_samples": 2},
            {"mode": "spaced", "samples": [[101]],
             "complete": True, "timer_value_changed": True, "expected_samples": 1}]}
        self.context = {"before": {"monotonic_s": 1.05}, "after": {"monotonic_s": 1.5}}
        self.capture = {"after_enable_monotonic": 1.0, "before_disable_monotonic": 2.0,
                        "cpu_stats": {"cpu6": "overrun: 0\ncommit overrun: 0\ndropped events: 0"}}

    def analyze(self, parsed=None):
        return trace_modes(self.probe, self.context, self.capture, parsed or graph())

    def test_nearest_rank_matches_probe_and_cpu_ranges(self):
        self.assertEqual(stats([1, 3])["p50_us"], 1)
        self.assertEqual(stats([1, 3])["p99_us"], 3)
        self.assertEqual(cpu_members("0-3, 6 7"), {0, 1, 2, 3, 6, 7})

    def test_exact_calls_partition_by_sequence_not_raw_clock(self):
        result = self.analyze()
        self.assertTrue(result["mode_assignment_valid"])
        continuous = result["by_mode"]["continuous"]
        self.assertEqual(continuous["bm_read32_us"]["count"], 2)
        self.assertEqual(continuous["same_root_get_reg_minus_read32_us"]["mean_us"], 1)
        self.assertEqual(result["by_mode"]["spaced"]["bm_read32_us"]["count"], 1)

    def test_missing_call_refuses_partition(self):
        result = self.analyze(graph(2))
        self.assertFalse(result["mode_assignment_valid"])
        self.assertEqual(result["by_mode"], {})

    def test_unrelated_pid_does_not_enter_probe_stats(self):
        parsed = graph()
        parsed["calls"].extend(graph(1, pid=99)["calls"])
        result = self.analyze(parsed)
        self.assertTrue(result["mode_assignment_valid"])
        self.assertEqual(result["other_pid_root_count"], 1)

    def test_loss_and_window_rejection(self):
        self.capture["cpu_stats"]["cpu6"] = "overrun: 1"
        self.assertFalse(self.analyze()["mode_assignment_valid"])
        self.capture["cpu_stats"]["cpu6"] = "overrun: 0"
        self.context["after"]["monotonic_s"] = 3
        self.assertFalse(self.analyze()["mode_assignment_valid"])

    def test_child_ambiguity_preserves_root_but_rejects_nested_breakdown(self):
        parsed = copy.deepcopy(graph())
        parsed["calls"][0]["inclusive_function_stats"]["bm_read32"]["count"] = 2
        result = self.analyze(parsed)
        self.assertTrue(result["mode_assignment_valid"])
        self.assertFalse(result["nested_breakdown_available"])

    def test_inlined_child_does_not_invalidate_root_duration(self):
        parsed = parse_trace("\n".join(
            f"{1.1 + index * .01:.6f} | 6) probe-42 | 5 us | bm_get_reg();"
            for index in range(3)), roots=["bm_get_reg"])
        result = self.analyze(parsed)
        self.assertTrue(result["root_trace_valid"])
        self.assertFalse(result["nested_breakdown_available"])
        continuous = result["by_mode"]["continuous"]
        self.assertEqual(continuous["bm_get_reg_us"]["count"], 2)
        self.assertIsNone(continuous["bm_read32_us"])
        self.assertIsNone(continuous["same_root_get_reg_minus_read32_us"])

    def test_negative_nested_remainder_is_not_reported_as_cost(self):
        parsed = graph()
        parsed["calls"][0]["duration_us"] = 4
        continuous = self.analyze(parsed)["by_mode"]["continuous"]
        self.assertEqual(continuous["nested_negative_remainders"], 1)
        self.assertIsNone(continuous["same_root_get_reg_minus_read32_us"])


if __name__ == "__main__":
    unittest.main()
