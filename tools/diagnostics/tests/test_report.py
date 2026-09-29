import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("wall_report", Path(__file__).resolve().parents[1] / "report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stream = {"stream_id": 0, "accounting_complete": True, "error": "",
                       "completed_measured": 20, "completed_fps": 2., "previews_submitted_measured": 10,
                       "dropped_stale": 1, "dropped_overwrite": 30,
                       "analysis_completion_continuity": {"seconds": 10., "no_results": False,
                           "max_gap_including_edges_s": .7}}
        self.summary = {"status": "measured", "accounting_complete": True, "error": "",
                        "measured_seconds": 10., "measurement_start_monotonic_s": 100.,
                        "observed_measurement_end_monotonic_s": 110., "total_completed_fps": 2.,
                        "streams": [self.stream]}
        self.config = {"device": 0, "duration_s": 10., "streams": 1, "inference_enabled": True}
        self.run = {"selected_devices": [0], "streams_by_device": {"0": 1}, "status": "stopped",
                    "exit_status": 0, "viewer_returncode": 0,
                    "workers": [{"device": 0, "status": "completed", "returncode": 0}]}
        self.write("run.json", self.run)
        self.write("device_0/config.json", self.config)
        self.write("device_0/summary.json", self.summary)

    def write(self, path, value):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value), encoding="utf-8")

    def test_missing_tpu_is_unknown_not_zero_and_drops_keep_scope(self):
        result = report.summarize(self.root)
        self.assertTrue(result["valid_measurement"])
        row = result["devices"]["0"]
        self.assertIsNone(row["formal_tpu"])
        self.assertEqual(row["mean_preview_submission_fps_per_channel"], 1.)
        self.assertEqual(row["stale_drops_whole_run"], 1)
        self.assertEqual(row["overwritten_frames_whole_run"], 30)
        self.assertTrue(result["warnings"])
        self.assertFalse(result["formal_tpu_complete"])
        rendered = report.markdown(result)
        self.assertIn("TPU 遥测不完整", rendered)
        self.assertIn("全运行覆盖等待帧", rendered)
        self.assertIn("| 1 | 30 |", rendered)
        self.assertNotIn("测量数据完整", rendered)

    def test_missing_worker_does_not_silently_report_partial_total(self):
        self.run["selected_devices"].append(1)
        self.run["streams_by_device"]["1"] = 1
        self.run["workers"].append({"device": 1, "status": "completed", "returncode": 0})
        self.write("run.json", self.run)
        result = report.summarize(self.root)
        self.assertFalse(result["valid_measurement"])
        self.assertIsNone(result["sum_of_device_mean_fps"])
        self.assertFalse(result["devices"]["1"]["measurement_complete"])

    def test_interrupted_supervisor_is_not_success_even_with_worker_summary(self):
        self.run["exit_status"] = 130
        self.write("run.json", self.run)
        self.assertFalse(report.summarize(self.root)["valid_measurement"])

    def test_reject_mismatched_counts_duration_nonfinite_and_accounting(self):
        cases = [("total_completed_fps", 3.), ("measured_seconds", 5.),
                 ("total_completed_fps", float("nan")), ("accounting_complete", False),
                 ("status", "running")]
        for key, value in cases:
            with self.subTest(key=key):
                summary = copy.deepcopy(self.summary)
                summary[key] = value
                with self.assertRaises(ValueError):
                    report.summarize_worker(summary, self.config, 1)

    def test_duplicate_channel_ids_are_rejected(self):
        summary = copy.deepcopy(self.summary)
        summary["streams"] *= 2
        config = dict(self.config, streams=2)
        with self.assertRaisesRegex(ValueError, "stream IDs"):
            report.summarize_worker(summary, config, 2)

    def test_zero_result_channel_is_not_acceptance(self):
        self.stream.update(completed_measured=0, completed_fps=0., previews_submitted_measured=0)
        self.stream["analysis_completion_continuity"].update(no_results=True, max_gap_including_edges_s=10.)
        self.summary["total_completed_fps"] = 0.
        self.write("device_0/summary.json", self.summary)
        result = report.summarize(self.root)
        self.assertFalse(result["valid_measurement"])
        self.assertIsNone(result["sum_of_device_mean_fps"])

    def test_wrong_formal_tpu_window_never_uses_all_run_average(self):
        tpu = {"samples": 2, "valid_samples": 2, "read_failures": 0,
               "worker_summary_status": "measured", "accounting_complete": True,
               "start_monotonic_s": 90., "end_monotonic_s": 110.,
               "mean_percent": 100., "min_percent": 100., "max_percent": 100., "sum_percent": 200.}
        self.write("telemetry-summary.json", {"devices": {"0": tpu}, "formal_measurement": {"devices": {"0": tpu}}})
        row = report.summarize(self.root)["devices"]["0"]
        self.assertIsNone(row["formal_tpu"])
        tpu["start_monotonic_s"] = 100.
        self.write("telemetry-summary.json", {"formal_measurement": {"devices": {"0": tpu}}})
        self.assertEqual(report.summarize(self.root)["devices"]["0"]["formal_tpu"]["mean_percent"], 100.)

    def test_malformed_optional_telemetry_stays_unknown(self):
        for formal in ([], {"devices": []}, {"devices": {"0": [100]}}):
            with self.subTest(formal=formal):
                self.write("telemetry-summary.json", {"formal_measurement": formal})
                result = report.summarize(self.root)
                self.assertTrue(result["valid_measurement"])
                self.assertIsNone(result["devices"]["0"]["formal_tpu"])
                self.assertTrue(result["warnings"])

    def test_existing_output_is_preserved(self):
        out = self.root / "saved"
        out.mkdir()
        marker = out / "report.md"
        marker.write_text("existing evidence", encoding="utf-8")
        with self.assertRaises(SystemExit) as failure:
            report.main([str(self.root), "--output", str(out)])
        self.assertEqual(failure.exception.code, 2)
        self.assertEqual(marker.read_text(encoding="utf-8"), "existing evidence")


if __name__ == "__main__":
    unittest.main()
