import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "observe.py"
with patch.object(sys, "path", [str(RUNNER_PATH.parent), *sys.path]):
    spec = importlib.util.spec_from_file_location("_hdmi_observe_runner", RUNNER_PATH)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)


class ObserveTests(unittest.TestCase):
    def parse(self, *extra):
        return runner.arguments(["--root", "/userdata/board repo", "--devices", "0,1", *extra])

    def plan(self, args):
        with patch.object(runner.sys, "platform", "win32"):
            return runner.build_plan(args)

    def multi_plan(self, plan, directory):
        config = Path(directory) / "device config.json"
        config.write_text(json.dumps(plan["device_configuration"]), encoding="utf-8")
        options = list(plan["run_command"][2:])
        options[options.index("--device-config") + 1] = str(config)
        args = runner.multi_run.arguments(options)
        with patch.object(runner.multi_run.single, "player_environment", return_value={}):
            return args, runner.multi_run.build_plan(args)

    def test_defaults_keep_32_background_channels_and_match_loaded_vs_baseline_inputs(self):
        loaded = self.plan(self.parse())
        baseline = self.plan(self.parse("--inference", "off"))
        for plan in (loaded, baseline):
            self.assertEqual(plan["streams_per_device"], 32)
            self.assertEqual(plan["duration_seconds"], 300)
            self.assertEqual(plan["material_seconds"], 600)
            self.assertEqual(plan["compare_stream_ids"], [0, 1])
            self.assertEqual(plan["observe_preview_fps"], 5)
            self.assertEqual(plan["record_mode"], "summary")
            self.assertEqual(plan["prime_local_decoders"], "on")
            self.assertIn("/data/results/hdmi-observe/", plan["device_config_path"])
            self.assertEqual(plan["device_configuration"]["context"]["entrypoint"], "observe")
        self.assertEqual(loaded["input"], baseline["input"])
        self.assertEqual(loaded["prepare_command"], baseline["prepare_command"])
        with tempfile.TemporaryDirectory() as directory:
            for plan in (loaded, baseline):
                args, multi = self.multi_plan(plan, directory)
                self.assertEqual(multi["total_streams"], 64)
                for worker in multi["workers"]:
                    self.assertEqual(worker["streams"], 32)
                    self.assertEqual(worker["compare_stream_ids"], [0, 1])
                    self.assertEqual((worker["policy"], worker["infer_fps"], worker["max_frame_age_ms"],
                                      worker["image_path"], worker["prime_local_decoders"]), ("latest", 0, 250, "auto", "on"))
                    if args.inference == "off":
                        self.assertEqual(worker["gate_merge_budget_kib"], 0)
                        self.assertNotIn("--bmodel", worker["worker_command"])
                        self.assertNotIn(worker["model"], runner.multi_run.required_files(args, multi))
                    else:
                        self.assertIn("--score-gate-model", worker["worker_command"])
                        self.assertIn(worker["model"], runner.multi_run.required_files(args, multi))

    def test_real_link_budgets_and_uniform_stream_override_apply_to_four_cards(self):
        links = [{"device": device, "available": True, "generation": 3 if device % 2 == 0 else 2,
                  "current_link_width": 2 if device % 2 == 0 else 1} for device in range(4)]
        with patch.object(runner.sys, "platform", "linux"), \
                patch.object(runner.showcase, "discover_devices", return_value=list(range(4))), \
                patch.object(runner.showcase, "device_link", side_effect=links):
            plan = runner.build_plan(self.parse("--devices", "auto", "--streams", "12", "--compare-streams", "11,0,5,7"))
        self.assertEqual(plan["devices"], list(range(4)))
        self.assertEqual(plan["compare_stream_ids"], [11, 0, 5, 7])
        self.assertEqual([options["streams"] for options in plan["device_configuration"]["devices"].values()], [12] * 4)
        self.assertEqual([options["gate_merge_budget_kib"] for options in plan["device_configuration"]["devices"].values()], [64, 128, 64, 128])

    def test_stop_skips_hardware_busy_checks_preparation_and_model_preflight(self):
        args = self.parse("--devices", "auto", "--stop", "--compare-streams", "ignored")
        with patch.object(runner.showcase, "discover_devices", side_effect=AssertionError("sysfs")), \
                patch.object(runner.showcase, "check_existing_run", side_effect=AssertionError("busy")), \
                patch.object(runner.showcase.subprocess, "run") as prepare, \
                patch.object(runner.showcase.os, "execv") as execute:
            plan = runner.build_plan(args)
            self.assertEqual(runner.showcase.execute(args, plan), 0)
            prepare.assert_not_called()
            self.assertEqual(execute.call_args.args[1][2:], ["--root", args.root, "--stop"])

    def test_dry_run_is_readonly_and_preserves_safe_argv_for_paths_with_spaces(self):
        root = "/userdata/board repo; $(touch never) 'quoted'"
        with patch.object(runner.sys, "platform", "win32"), \
                patch.object(runner.showcase, "execute", side_effect=AssertionError("execute")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                patch("builtins.open", side_effect=AssertionError("file access")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main(["--root", root, "--devices", "0,1", "--inference", "off", "--dry-run"]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["run_command"][1], root + "/demos/hdmi_wall/multi_run.py")
        self.assertEqual(plan["input"], root + "/data/inputs/hdmi_wall_demo_loop_600s.mp4")
        self.assertEqual(plan["inference"], "off")

    def test_invalid_selection_ranges_rates_and_stream_counts_are_rejected(self):
        for options in (("--streams", "1"), ("--streams", "33"), ("--compare-streams", "0,0"),
                        ("--compare-streams", "32"), ("--compare-streams", "0,1,2,3,4"),
                        ("--observe-preview-fps", "0"), ("--observe-preview-fps", "nan"),
                        ("--observe-preview-fps", "10.1"), ("--duration", "0"), ("--stop", "--dry-run")):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parse(*options)
        args = self.parse("--streams", "1", "--compare-streams", "0", "--observe-preview-fps", "0.5")
        self.assertEqual(args.compare_stream_ids, [0])
        self.assertEqual(args.observe_preview_fps, 0.5)


if __name__ == "__main__":
    unittest.main()
