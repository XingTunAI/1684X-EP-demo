import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, call, patch

script = Path(__file__).resolve().parents[3] / "scripts/prepare_hdmi_loop.py"
spec = importlib.util.spec_from_file_location("loop_material", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

INFO = {"codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
        "r_frame_rate": "25/1", "duration_seconds": 24.67, "color_space": "bt709", "color_range": "tv"}


class FlushedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1
        super().flush()


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

    def test_sha_progress_hashes_every_byte_with_throttled_flushed_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "long.mp4"
            payload = bytes(range(256)) * (5 * 4096)  # Five 1 MiB reads.
            path.write_bytes(payload)
            error = FlushedOutput()
            output = io.StringIO()
            # Reading chunks before the 2-second threshold must not spam logs;
            # the final completion line may follow the last update immediately.
            with patch.object(module.time, "monotonic", side_effect=[0, .5, 1.9, 2, 2.1, 4.1, 4.2]), \
                    patch.object(module.sys, "stderr", error), contextlib.redirect_stdout(output):
                result = module.sha256(path)
            self.assertEqual(result, hashlib.sha256(payload).hexdigest())
            self.assertEqual(output.getvalue(), "")
            lines = error.getvalue().splitlines()
            self.assertEqual(len(lines), 4)
            self.assertIn("Checking SHA-256", lines[0])
            updates = [line for line in lines if "SHA-256 progress" in line]
            self.assertEqual(len(updates), 2)
            self.assertIn("3,145,728 / 5,242,880 bytes (60.0%), 2.0s elapsed", updates[0])
            self.assertIn("5,242,880 / 5,242,880 bytes (100.0%), 4.1s elapsed", updates[1])
            self.assertIn("SHA-256 complete", lines[-1])
            self.assertEqual(error.flush_count, len(lines))

    def test_fast_and_empty_hashes_have_one_immediate_stage_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.mp4"
            for payload in (b"small complete content", b""):
                path.write_bytes(payload)
                error = FlushedOutput()
                with patch.object(module.time, "monotonic", side_effect=[10, 10.01, 10.02]), patch.object(module.sys, "stderr", error):
                    self.assertEqual(module.sha256(path), hashlib.sha256(payload).hexdigest())
                self.assertEqual(len(error.getvalue().splitlines()), 1)
                self.assertIn(f"({len(payload):,} bytes; full file verification)", error.getvalue())
                self.assertEqual(error.flush_count, 1)

    def test_copy_heartbeat_waits_between_reports_and_stops_without_extra_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"packets")
            stop = Mock()
            stop.wait.side_effect = [False, False, True]
            error = FlushedOutput()
            with patch.object(module.time, "monotonic", side_effect=[12, 14.1]), patch.object(module.sys, "stderr", error):
                module.copy_progress(stop, path, 10)
            self.assertEqual(stop.wait.call_args_list, [call(2.0), call(2.0), call(2.0)])
            lines = error.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("Generating local clip / finalizing MP4: 2.0s elapsed", lines[0])
            self.assertIn("7 bytes in the temporary file", lines[1])
            self.assertEqual(error.flush_count, 2)

    def test_copy_failure_stops_heartbeat_and_preserves_subprocess_error(self):
        for failure in (subprocess.CalledProcessError(3, ["ffmpeg"]), KeyboardInterrupt()):
            with self.subTest(error=type(failure).__name__):
                stop, thread = Mock(), Mock()
                with patch.object(module.threading, "Event", return_value=stop), \
                        patch.object(module.threading, "Thread", return_value=thread), \
                        patch.object(module.subprocess, "run", side_effect=failure), \
                        self.assertRaises(type(failure)) as caught:
                    module.run_copy(["ffmpeg"], {}, Path("unused.mp4"))
                self.assertIs(caught.exception, failure)
                thread.start.assert_called_once_with()
                stop.set.assert_called_once_with()
                thread.join.assert_called_once_with()

    def test_interrupted_copy_keeps_temporary_file_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            def interrupted(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial output")
                raise KeyboardInterrupt
            with self.dependencies(plan), patch.object(module.subprocess, "run", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                module.prepare(plan)
            self.assertFalse(Path(plan["output"]).exists())
            self.assertFalse(Path(plan["manifest"]).exists())
            self.assertFalse(Path(plan["temporary_output"]).parent.exists())

    def test_successful_cli_stdout_remains_only_the_absolute_path(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.fixture(directory)
            output = io.StringIO()
            with self.dependencies(plan), patch.object(module, "arguments", return_value=argparse.Namespace(dry_run=False)), \
                    patch.object(module, "build_plan", return_value=plan), patch.object(module.sys, "platform", "linux"), \
                    patch.object(module.signal, "signal"), contextlib.redirect_stdout(output):
                self.assertEqual(module.main([]), 0)
                progress = module.sys.stderr.getvalue()
            self.assertEqual(output.getvalue(), str(Path(plan["output"]).absolute()) + "\n")
            self.assertIn("Inspecting source video", progress)
            self.assertIn("Generating approximately 600s", progress)
            self.assertIn("Publishing verified material", progress)


if __name__ == "__main__":
    unittest.main()
