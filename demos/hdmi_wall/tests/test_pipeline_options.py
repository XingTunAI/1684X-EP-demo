import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run
import multi_run
import showcase


class PipelineOptionsTests(unittest.TestCase):
    def plan(self, *options):
        args = showcase.arguments(["--root", "/board repo", "--devices", "0,2", *options])
        with patch.object(showcase.sys, "platform", "win32"):
            return showcase.build_plan(args)

    def test_material_paths_remain_one_argument_and_cache_is_distinct(self):
        a = self.plan("--input", "data/my highway.mp4", "--streams", "24")
        b = self.plan("--input", "data/another.mp4")
        default = self.plan()
        command = a["prepare_command"]
        self.assertEqual(command[command.index("--input") + 1], "/board repo/data/my highway.mp4")
        self.assertEqual(command[command.index("--output") + 1], a["input"])
        self.assertEqual(len({a["input"], b["input"], default["input"]}), 3)
        self.assertTrue(all(v["streams"] == 24 for v in a["device_configuration"]["devices"].values()))

    def test_both_modes_reach_workers_with_native_decode_and_preview_policy(self):
        for mode, preview, wall in (("showcase", -1, 0), ("stress", 3, 0)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                plan = self.plan("--mode", mode, "--retrieve-every", "2")
                config = Path(directory) / "device configuration.json"
                config.write_text(json.dumps(plan["device_configuration"]), encoding="utf-8")
                options = plan["run_command"][2:]
                options[options.index("--device-config") + 1] = str(config)
                args = multi_run.arguments(options)
                self.assertEqual(args.display_fps, 30 if mode == "showcase" else 10)
                for worker in multi_run.build_plan(args)["workers"]:
                    command = worker["worker_command"]
                    for key, value in (("decoder", "linear"), ("decoder-buffers", "8"),
                                       ("retrieve-every", "2"), ("output-buffer", "reuse")):
                        self.assertEqual(command[command.index("--" + key) + 1], value)
                    self.assertEqual(worker["preview_fps"], preview)
                    self.assertEqual(worker["wall_fps"], wall)
                    self.assertEqual(command[command.index("--local-eof") + 1], "loop" if mode == "showcase" else "fail")
                    self.assertNotIn("LD_PRELOAD", " ".join(command))

    def test_invalid_buffers_stride_combination_and_network_material_are_rejected(self):
        for parser, options in ((run.arguments, ["--decoder-buffers", "16"]),
                                (multi_run.arguments, ["--retrieve-every", "2", "--observe-decode", "on"]),
                                (showcase.arguments, ["--input", "rtsp://camera"]),
                                (showcase.arguments, ["--streams", "33"]),
                                (showcase.arguments, ["--wall-fps", "121"])):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser(["--root", "/board", *options])

    def test_display_refresh_reaches_viewer_without_changing_preview_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.plan("--display-fps", "30", "--preview-fps", "-1")
            config = Path(directory) / "devices.json"
            config.write_text(json.dumps(plan["device_configuration"]), encoding="utf-8")
            options = plan["run_command"][2:]
            options[options.index("--device-config") + 1] = str(config)
            args = multi_run.arguments(options)
            self.assertEqual(args.display_fps, 30)
            self.assertEqual(args.preview_fps, -1)
            multi = multi_run.build_plan(args)
            manifests = [v for v in multi.values() if isinstance(v, dict) and "fps" in v]
            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0]["fps"], 30)

    def test_legacy_capture_remains_explicitly_selectable(self):
        plan = self.plan("--decoder", "opencv", "--preview-fps", "7", "--wall-fps", "12")
        command = plan["run_command"]
        self.assertEqual(command[command.index("--decoder") + 1], "opencv")
        self.assertEqual(command[command.index("--preview-fps") + 1], "7.0")


if __name__ == "__main__":
    unittest.main()
