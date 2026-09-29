"""Check historical profiles without hardware access."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "legacy"))
import preview_experiments as experiments


class PreviewExperimentTests(unittest.TestCase):
    def exercise(self, profile, extra=(), failure=False):
        args = ["--output", "unused-output", "--input", "unused-input", "--repeats", "2", *extra]
        with patch.object(experiments, "preflight", return_value={}) as preflight, patch.object(experiments, "save") as save, patch.object(experiments, "report"), patch.object(experiments, "summarize", side_effect=lambda *a: {}), patch.object(experiments, "run_stage", side_effect=RuntimeError("interrupted") if failure else None) as stage, patch.object(experiments.threading, "Thread"), patch.object(experiments.signal, "signal"), patch.object(Path, "mkdir"), patch.object(Path, "read_bytes", return_value=b"binary"), patch.object(Path, "read_text", return_value=json.dumps({"streams": [{"analysis_completion_continuity": {"max_gap_including_edges_s": .2}}]})), contextlib.redirect_stdout(io.StringIO()):
            if failure:
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    experiments.main(profile, args)
            else:
                experiments.main(profile, args)
            return stage.call_args_list, save.call_args_list, preflight.call_args.args[0]

    def test_profiles_preserve_order_rates_and_binary_selection(self):
        expected = {
            "budget": [("sync10", 10), ("sync3", 3), ("async3", 3), ("sync5", 5), ("async5", 5)],
            "async": [("sync", 10), ("async", 10)],
            "vpp": [("limit0", 10), ("limit2", 10), ("limit4", 10)],
            "stages": [("original", 10), ("none", 0), ("vpp", 10), ("readback", 10), ("display", 10)],
        }
        for profile, cases in expected.items():
            with self.subTest(profile=profile):
                calls, saved, config = self.exercise(profile)
                order = [(0, c) for c in cases] + [(1, c) for c in reversed(cases)]
                self.assertEqual(len(calls), len(order))
                for call, (repeat, (name, rate)) in zip(calls, order):
                    command = call.args[2]
                    self.assertEqual(call.args[1], f"r{repeat}_{name}")
                    self.assertEqual(command[command.index("--preview-fps") + 1], str(rate))
                    self.assertTrue(call.kwargs["readback"])
                    self.assertEqual(command[command.index("--streams") + 1], "32")
                    binary_index = 2 if profile == "vpp" else 0
                    build = "build" if name == "original" else {"budget": "build-async", "async": "build-async", "vpp": "build-vpp-limit", "stages": "build-ablation"}[profile]
                    self.assertEqual(Path(command[binary_index]).parent.name, build)
                    if profile == "vpp":
                        self.assertEqual(command[:2], ["env", "HDMI_VPP_LIMIT=" + name[5:]])
                    if profile == "stages":
                        self.assertEqual("--preview-stage" in command, name != "original")
                self.assertEqual(saved[-1].args[1], {"status": "completed"})

    def test_link_profiles_keep_expected_speed_and_reverse(self):
        for profile, names in (("link", ["async3", "sync10", "none"]), ("same-card", ["sync10", "none"])):
            with self.subTest(profile=profile):
                calls, _, config = self.exercise(profile, ["--expected-link-speed", "8.0", "--reverse"])
                self.assertEqual(config.expected_link_speed, "8.0")
                self.assertEqual([c.args[1] for c in calls[:len(names)]], ["r0_" + n for n in names])

    def test_failed_stage_never_writes_completed(self):
        _, saved, _ = self.exercise("async", failure=True)
        self.assertEqual(saved[-1].args[1]["status"], "failed")
        self.assertFalse(any(c.args[1] == {"status": "completed"} for c in saved))


if __name__ == "__main__":
    unittest.main()
