import importlib.util
from pathlib import Path
import sys
import unittest


PATH = Path(__file__).resolve().parents[1] / "analyze_dispatch_graph.py"
SPEC = importlib.util.spec_from_file_location("analyze_dispatch_graph", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
parse_trace = MODULE.parse_trace


def event(time, body, duration="", tid=42, cpu=1, depth=0):
    return f"{time:.6f} | {cpu}) task-{tid} | {duration} |  {'  ' * depth}{body}"


class DispatchGraphTests(unittest.TestCase):
    def test_nested_vpp_durations_not_double_counted_and_cpu_migration(self):
        trace = "\n".join([
            event(1, "bm1686_trigger_vpp [bmsophon]() {"),
            event(1.001, "down_interruptible() {", depth=1),
            event(1.002, "schedule_timeout();", "1000 us", depth=2),
            event(1.004, "}", "3000 us", depth=1, cpu=3),
            event(1.004, "bmdev_memcpy_s2d_internal() {", depth=1),
            event(1.0041, "bm1684_cdma_transfer() {", depth=2),
            event(1.0042, "mutex_lock();", "100 us", depth=3),
            event(1.0044, "} /* bm1684_cdma_transfer */", "300 us", depth=2),
            event(1.0045, "}", "500 us", depth=1),
            event(1.0045, "schedule_timeout();", "200 us", depth=1),
            event(1.005, "} /* bm1686_trigger_vpp [bmsophon] */", "5000 us"),
        ])
        result = parse_trace(trace)
        root = result["calls"][0]
        self.assertEqual(root["cpus"], [1, 3])
        self.assertEqual(root["return_timestamp"], "1.005000")
        stages = root["stages"]
        self.assertEqual(stages["children"]["vpp_admission"]["inclusive_us"], 3000)
        self.assertEqual(stages["children"]["vpp_completion_wait"]["inclusive_us"], 200)
        descriptor = stages["children"]["descriptor_upload"]
        self.assertEqual(descriptor["inclusive_us"], 500)
        cdma = descriptor["children"]["cdma_call"]
        self.assertEqual(cdma["children"]["direct_mutex_calls"]["inclusive_us"], 100)
        self.assertEqual(stages["unclassified_remainder_us"], 1300)
        functions = root["inclusive_function_stats"]
        self.assertEqual(functions["schedule_timeout"]["count"], 2)
        self.assertEqual(functions["schedule_timeout"]["sum_us"], 1200)
        # The overlapping function inventory is separate from stage accounting.
        self.assertEqual(functions["bm1686_trigger_vpp"]["sum_us"], 5000)

    def test_leaf_root_has_no_fabricated_return_timestamp(self):
        result = parse_trace(event(1, "bmdrv_send_api();", "76 us"))
        call = result["calls"][0]
        self.assertTrue(call["leaf"])
        self.assertIsNone(call["return_timestamp"])
        self.assertEqual(call["duration_us"], 76)

    def test_trace_edges_discarded_and_other_tid_survives(self):
        trace = "\n".join([
            event(1, "} /* bm1686_trigger_vpp */", "100 us"),
            event(2, "bm1686_trigger_vpp() {"),
            event(2, "bmdev_memcpy_d2s() {", tid=43),
            event(2.001, "}", "1000 us", tid=43),
            event(2.001, "down_interruptible() {", depth=1),
        ])
        result = parse_trace(trace)
        self.assertEqual(len(result["calls"]), 1)
        self.assertEqual(result["calls"][0]["root"], "bmdev_memcpy_d2s")
        self.assertEqual(result["diagnostics"]["orphan_returns"], 1)
        self.assertEqual(result["diagnostics"]["unclosed_functions"], 2)
        self.assertEqual(result["diagnostics"]["discarded_incomplete_roots"], 1)

    def test_mismatch_discards_root_and_parser_recovers(self):
        trace = "\n".join([
            event(1, "bm1686_trigger_vpp() {"),
            event(1.001, "} /* wrong_function */", "1000 us"),
            event(2, "bmdrv_send_api();", "80 us"),
        ])
        result = parse_trace(trace)
        self.assertEqual(result["diagnostics"]["mismatched_returns"], 1)
        self.assertEqual(len(result["calls"]), 1)

    def test_submit_all_direct_mutex_calls_and_not_nested_fifo_mutex(self):
        trace = "\n".join([
            event(1, "bmdrv_send_api() {"),
            event(1, "mutex_lock();", "10 us", depth=1),
            event(1, "mutex_lock();", "2 us", depth=1),
            event(1, "bmdev_wait_msgfifo() {", depth=1),
            event(1, "mutex_lock();", "3 us", depth=2),
            event(1, "}", "8 us", depth=1),
            event(1, "mutex_lock();", "1 us", depth=1),
            event(1, "bmdev_copy_to_msgfifo();", "20 us", depth=1),
            event(1.0001, "}", "100 us"),
        ])
        stages = parse_trace(trace)["calls"][0]["stages"]
        self.assertEqual(stages["children"]["direct_submission_mutex_calls"]["inclusive_us"], 13)
        self.assertEqual(stages["children"]["direct_submission_mutex_calls"]["matched_calls"], 3)
        self.assertEqual(stages["unclassified_remainder_us"], 59)

    def test_stage_buffer_completion_is_not_cdma_completion(self):
        trace = "\n".join([
            event(1, "bmdev_memcpy_d2s() {"),
            event(1, "bmdrv_get_stagemem() {", depth=1),
            event(1, "wait_for_completion_timeout();", "50 us", depth=2),
            event(1, "}", "60 us", depth=1),
            event(1, "bm1684_cdma_transfer() {", depth=1),
            event(1, "mutex_lock();", "100 us", depth=2),
            event(1, "wait_for_completion_timeout();", "200 us", depth=2),
            event(1, "}", "400 us", depth=1),
            event(1.0005, "}", "500 us"),
        ])
        stages = parse_trace(trace)["calls"][0]["stages"]
        self.assertEqual(stages["unclassified_remainder_us"], 40)
        self.assertEqual(stages["children"]["cdma_call"]["children"]["completion_wait"]["inclusive_us"], 200)

    def test_lost_event_marker_discards_active_root(self):
        trace = "\n".join([event(1, "bmdev_memcpy_d2s() {"),
                           "CPU:1 [LOST 200 EVENTS]", event(2, "}", "1 s")])
        result = parse_trace(trace)
        self.assertEqual(result["calls"], [])
        self.assertEqual(result["diagnostics"]["lost_event_markers"], 1)

    def test_percentiles_and_unit_conversion(self):
        result = parse_trace("\n".join([
            event(1, "bmdrv_send_api();", "1000 ns"),
            event(2, "bmdrv_send_api();", "0.003 ms"),
        ]))
        stats = result["summary"]["bmdrv_send_api"]["stages"]["inclusive_us"]
        self.assertEqual(stats["mean_us"], 2)
        self.assertEqual(stats["p50_us"], 2)
        self.assertAlmostEqual(stats["p95_us"], 2.9)
        functions = result["summary"]["bmdrv_send_api"]["inclusive_function_stats"]
        self.assertEqual(functions["bmdrv_send_api"]["count"], 2)
        self.assertEqual(functions["bmdrv_send_api"]["mean_us"], 2)

    def test_named_return_after_collapsed_leaf_is_not_counted_twice(self):
        # Seen in a real cross-CPU function_graph text dump: a child rendered
        # as a leaf later has a separate return. This subtree is ambiguous.
        trace = "\n".join([
            event(1, "bm1686_trigger_vpp() {"),
            event(1, "schedule_timeout();", "600 us", depth=1),
            event(1.0005, "} /* schedule_timeout */", "500 us", depth=1, cpu=2),
            event(1.0007, "} /* bm1686_trigger_vpp */", "700 us", cpu=2),
        ])
        result = parse_trace(trace)
        self.assertEqual(result["calls"], [])
        self.assertEqual(result["diagnostics"]["discarded_incomplete_roots"], 1)

    def test_nested_selected_root_only_emits_outermost(self):
        trace = "\n".join([
            event(1, "bm1686_trigger_vpp() {"),
            event(1, "bmdev_memcpy_s2d();", "500 us", depth=1),
            event(1.001, "}", "1000 us"),
        ])
        result = parse_trace(trace)
        self.assertEqual(len(result["calls"]), 1)
        self.assertEqual(result["summary"]["bmdev_memcpy_s2d"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
