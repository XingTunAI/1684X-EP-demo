import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

script = Path(__file__).resolve().parents[3] / "scripts/prepare_hdmi_loop.py"
spec = importlib.util.spec_from_file_location("loop_material", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

INFO = {"codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
        "r_frame_rate": "25/1", "duration_seconds": 24.67, "color_space": "bt709", "color_range": "tv"}


class MaterialTests(unittest.TestCase):
    def fixture(self, directory):
        args = argparse.Namespace(root=directory, input="source.mp4", seconds=600, output=None)
        plan = module.build_plan(args)
        Path(plan["input"]).write_bytes(b"source video packets")
        return plan

    def dependencies(self, plan, *, failed=False, race=False, free_bytes=10 * 1024 ** 3, source_duration=None):
        stack = contextlib.ExitStack()
        original_is_file = Path.is_file

        def is_file(path):
            return True if path.as_posix() in ("/usr/bin/ffmpeg", "/usr/bin/ffprobe") else original_is_file(path)

        def generate(command, **kwargs):
            self.assertEqual(kwargs["env"]["LD_LIBRARY_PATH"], module.SYSTEM_LIBS)
            self.assertNotIn("LD_PRELOAD", kwargs["env"])
            Path(command[-1]).write_bytes(b"repeated video packets")
            if failed:
                raise subprocess.CalledProcessError(1, command)
            if race:
                Path(plan["output"]).write_bytes(b"unrelated user file")
            return subprocess.CompletedProcess(command, 0)

        stack.enter_context(patch.object(module.Path, "is_file", new=is_file))
        stack.enter_context(patch.object(module.os, "access", return_value=True))
        source_info = dict(INFO)
        if source_duration is not None:
            source_info["duration_seconds"] = source_duration
        stack.enter_context(patch.object(module, "probe", side_effect=[source_info, dict(INFO, duration_seconds=600)]))
        stack.enter_context(patch.object(module.subprocess, "run", side_effect=generate))
        stack.enter_context(patch.object(module.shutil, "disk_usage", return_value=argparse.Namespace(free=free_bytes)))
        stack.enter_context(patch("sys.stderr", new_callable=io.StringIO))
        return stack

    def test_plan_uses_packet_copy_and_isolates_system_libraries(self):
        args = module.arguments(["--root", "/userdata/repo", "--seconds", "900", "--dry-run"])
        plan = module.build_plan(args)
        self.assertTrue(plan["output"].endswith("hdmi_wall_demo_loop_900s.mp4"))
        self.assertEqual(plan["copy_command"][0], "/usr/bin/ffmpeg")
        self.assertEqual(plan["copy_command"][plan["copy_command"].index("-c") + 1], "copy")
        self.assertNotIn("-r", plan["copy_command"])
        with patch.dict(module.os.environ, {"LD_LIBRARY_PATH": "/vendor", "LD_PRELOAD": "/vendor.so", "LD_AUDIT": "/audit.so"}):
            env = module.system_environment()
            self.assertEqual(env["LD_LIBRARY_PATH"], module.SYSTEM_LIBS)
            self.assertNotIn("LD_PRELOAD", env)
            self.assertNotIn("LD_AUDIT", env)

    def test_success_publishes_hashed_output_and_removes_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            with self.dependencies(plan):
                result = module.prepare(plan)
            self.assertEqual(result, Path(plan["output"]))
            metadata = json.loads(Path(plan["manifest"]).read_text())
            self.assertEqual(metadata["source_sha256"], module.sha256(Path(plan["input"])))
            self.assertEqual(metadata["output_sha256"], module.sha256(result))
            self.assertFalse(Path(plan["temporary_output"]).parent.exists())

    def test_only_verified_matching_output_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            with self.dependencies(plan):
                module.prepare(plan)
            with self.dependencies(plan), patch.object(module.subprocess, "run") as process:
                self.assertEqual(module.prepare(plan), Path(plan["output"]))
                process.assert_not_called()
            Path(plan["input"]).write_bytes(b"different source")
            with self.dependencies(plan), self.assertRaisesRegex(ValueError, "refusing overwrite"):
                module.prepare(plan)

    def test_insufficient_space_reports_estimate_without_starting_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            with self.dependencies(plan, free_bytes=0), patch.object(module.subprocess, "run") as process, \
                    self.assertRaisesRegex(ValueError, "Insufficient free space") as failure:
                module.prepare(plan)
            process.assert_not_called()
            self.assertIn("estimated output", str(failure.exception))
            self.assertIn("available 0 bytes", str(failure.exception))
            self.assertIn("5% + 1 GiB reserve", str(failure.exception))
            self.assertFalse(Path(plan["output"]).exists())
            self.assertFalse(Path(plan["manifest"]).exists())
            self.assertFalse(Path(plan["temporary_output"]).parent.exists())

    def test_verified_cache_is_reused_even_with_no_free_disk_space(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            with self.dependencies(plan):
                module.prepare(plan)
            with self.dependencies(plan, free_bytes=0), patch.object(module.subprocess, "run") as process:
                result = module.prepare(plan)
                module.shutil.disk_usage.assert_not_called()
            process.assert_not_called()
            self.assertEqual(result, Path(plan["output"]))

    def test_exact_space_estimate_plus_reserve_allows_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            # 20-byte source / 10 seconds * 600 seconds = 1,200 bytes.
            # The threshold must include another 60 bytes and a full 1 GiB.
            self.assertEqual(Path(plan["input"]).stat().st_size, 20)
            required = 1200 + 60 + 1024 ** 3
            with self.dependencies(plan, free_bytes=required, source_duration=10):
                result = module.prepare(plan)
                module.subprocess.run.assert_called_once()
                module.shutil.disk_usage.assert_called_once_with(Path(directory))
                self.assertIn("estimated output 1,200 bytes", module.sys.stderr.getvalue())
                self.assertIn(f"available {required:,} bytes", module.sys.stderr.getvalue())
            self.assertTrue(result.is_file())
            self.assertTrue(Path(plan["manifest"]).is_file())
            self.assertFalse(Path(plan["temporary_output"]).parent.exists())

    def test_unknown_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            output = Path(plan["output"])
            output.parent.mkdir(parents=True)
            output.write_bytes(b"unknown")
            with self.dependencies(plan), self.assertRaisesRegex(ValueError, "refusing overwrite"):
                module.prepare(plan)
            self.assertEqual(output.read_bytes(), b"unknown")

    def test_failed_encoder_cleans_up_only_its_temporary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            with self.dependencies(plan, failed=True), self.assertRaises(subprocess.CalledProcessError):
                module.prepare(plan)
            self.assertFalse(Path(plan["output"]).exists())
            self.assertFalse(Path(plan["manifest"]).exists())
            self.assertFalse(Path(plan["temporary_output"]).parent.exists())

    def test_destination_race_preserves_unknown_file_and_rolls_back_own_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            with self.dependencies(plan, race=True), self.assertRaises(FileExistsError):
                module.prepare(plan)
            self.assertEqual(Path(plan["output"]).read_bytes(), b"unrelated user file")
            self.assertFalse(Path(plan["manifest"]).exists())
            self.assertFalse(Path(plan["temporary_output"]).parent.exists())

    def test_output_verification_rejects_changed_fps_or_short_duration(self):
        for changed in ({"r_frame_rate": "30/1"}, {"duration_seconds": 590}, {"color_space": "bt470bg"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                module.verify_output(INFO, dict(INFO, **changed), 600)


if __name__ == "__main__":
    unittest.main()
