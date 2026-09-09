import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "benchmark.py"
with patch.object(sys, "path", [str(RUNNER_PATH.parent), *sys.path]):
    spec = importlib.util.spec_from_file_location("_hdmi_benchmark_runner", RUNNER_PATH)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)


class BenchmarkTests(unittest.TestCase):
    def parse(self, *extra):
        return runner.arguments(["--root", "/userdata/board repo", *extra])

    def test_default_30_and_32_override_keep_fixed_settings_and_safe_argv(self):
        root = "/userdata/board repo; $(touch should-not-exist) 'quoted'"
        for streams in (30, 32):
            with self.subTest(streams=streams):
                args = self.parse("--root", root, *([] if streams == 30 else ["--streams", "32"]))
                plan = runner.build_plan(args)
                self.assertEqual((plan["devices"], plan["streams_per_device"], plan["duration_seconds"],
                                  plan["material_seconds"]), ([1], streams, 300, 600))
                self.assertEqual(plan["run_command"][-len(runner.FIXED_OPTIONS):], runner.FIXED_OPTIONS)
                self.assertEqual(plan["gate_merge_budget_kib"], 0)
                self.assertEqual(plan["run_command"][1], root + "/demos/hdmi_wall/multi_run.py")
                validated = runner.multi_run.arguments(plan["run_command"][2:])
                self.assertEqual(validated.root, root)
                self.assertEqual(validated.input, root + "/data/inputs/hdmi_wall_demo_loop_600s.mp4")
                self.assertEqual((validated.model, validated.score_gate, validated.policy,
                                  validated.infer_fps, validated.max_frame_age_ms, validated.image_path),
                                 ("s", "on", "latest", 0, 250, "auto"))

    def test_explicit_gate_merge_budget_keeps_other_baseline_settings(self):
        for budget in (0, 64, 1024):
            with self.subTest(budget=budget):
                plan = runner.build_plan(self.parse("--gate-merge-budget-kib", str(budget), "--devices", "0,1"))
                self.assertEqual(plan["gate_merge_budget_kib"], budget)
                self.assertEqual(plan["run_command"].count("--gate-merge-budget-kib"), 1)
                self.assertEqual(plan["run_command"][-len(runner.FIXED_OPTIONS):], runner.FIXED_OPTIONS)
                args = runner.multi_run.arguments(plan["run_command"][2:])
                self.assertEqual(args.gate_merge_budget_kib, budget)
                self.assertEqual((args.model, args.score_gate, args.streams, args.duration), ("s", "on", 30, 300))
                with patch.object(runner.multi_run.single, "player_environment", return_value={}):
                    workers = runner.multi_run.build_plan(args)["workers"]
                self.assertEqual([worker["gate_merge_budget_kib"] for worker in workers], [budget, budget])

    def test_material_length_rounding_and_short_measurement_windows(self):
        for duration, expected in ((1, 600), (300, 600), (537, 600), (538, 1200),
                                   (600, 1200), (900, 1200), (1800, 2400)):
            with self.subTest(duration=duration):
                plan = runner.build_plan(self.parse("--duration", str(duration)))
                self.assertEqual(plan["material_seconds"], expected)
                self.assertGreaterEqual(expected, duration + 3 + 60)
                self.assertEqual(plan["prepare_command"][-2:], ["--seconds", str(expected)])
                multi_args = runner.multi_run.arguments(plan["run_command"][2:])
                with patch.object(runner.multi_run.single, "player_environment", return_value={}):
                    worker = runner.multi_run.build_plan(multi_args)["workers"][0]["worker_command"]
                self.assertEqual(worker[worker.index("--window") + 1], str(min(10, duration)))

    def test_experimental_priming_defaults_off_and_reaches_all_workers(self):
        for mode in ("off", "on"):
            with self.subTest(mode=mode):
                plan = runner.build_plan(self.parse("--devices", "0,1", *([] if mode == "off" else ["--prime-local-decoders", mode])))
                self.assertEqual(plan["prime_local_decoders"], mode)
                self.assertEqual(plan["run_command"][-len(runner.FIXED_OPTIONS):], runner.FIXED_OPTIONS)
                args = runner.multi_run.arguments(plan["run_command"][2:])
                with patch.object(runner.multi_run.single, "player_environment", return_value={}):
                    multi = runner.multi_run.build_plan(args)
                self.assertEqual(multi["prime_local_decoders"], mode)
                for worker in multi["workers"]:
                    self.assertEqual(worker["prime_local_decoders"], mode)
                    command = worker["worker_command"]
                    self.assertEqual(command[command.index("--prime-local-decoders") + 1], mode)

    def test_invalid_parameters_are_rejected(self):
        cases = (["--streams", "0"], ["--streams", "33"], ["--duration", "0"],
                 ["--devices", "1,1"], ["--devices", "0,1,2,3,4"], ["--root", "relative"],
                 ["--gate-merge-budget-kib", "-1"], ["--gate-merge-budget-kib", "1025"],
                 ["--gate-merge-budget-kib", "1.5"],
                 ["--model", "n"], ["--stop", "--dry-run"])
        for options in cases:
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    self.parse(*options)
                self.assertEqual(caught.exception.code, 2)

    def test_stop_executes_no_preparation(self):
        args = self.parse("--stop")
        plan = runner.build_plan(args)
        with patch.object(runner.subprocess, "run") as prepare, patch.object(runner.os, "execv") as execute:
            self.assertEqual(runner.execute(args, plan), 0)
            prepare.assert_not_called()
            self.assertEqual(execute.call_args.args[1][2:], ["--root", "/userdata/board repo", "--stop"])

    def test_prepare_failure_propagates_without_launching_workers(self):
        args = self.parse()
        plan = runner.build_plan(args)
        for code, expected in ((7, 7), (-2, 130)):
            with self.subTest(code=code), \
                    patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], code)), \
                    patch.object(runner.os, "execv") as execute:
                self.assertEqual(runner.execute(args, plan), expected)
                execute.assert_not_called()

    def test_success_replaces_wrapper_without_shell_or_environment_override(self):
        args = self.parse()
        plan = runner.build_plan(args)
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as prepare, \
                patch.object(runner.os, "execv") as execute:
            self.assertEqual(runner.execute(args, plan), 0)
            prepare.assert_called_once_with(plan["prepare_command"], cwd=args.root, check=False)
            self.assertNotIn("shell", prepare.call_args.kwargs)
            self.assertNotIn("env", prepare.call_args.kwargs)
            execute.assert_called_once_with(plan["run_command"][0], plan["run_command"])

    def test_dry_run_does_not_access_files_or_start_processes(self):
        with patch.object(runner.subprocess, "run", side_effect=AssertionError("subprocess")), \
                patch.object(runner.os, "execv", side_effect=AssertionError("exec")), \
                patch.object(Path, "is_file", side_effect=AssertionError("file probe")), \
                patch("builtins.open", side_effect=AssertionError("file access")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main(["--root", "/nonexistent/board repo", "--dry-run"]), 0)
            self.assertEqual(json.loads(output.getvalue())["streams_per_device"], 30)


if __name__ == "__main__":
    unittest.main()
