"""Exercise historical launch configuration without running hardware or threads."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "legacy"))
import linear_source


class LinearSourceTests(unittest.TestCase):
    def test_video_hash_streams_multiple_blocks_without_read_bytes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "video.bin"
            data = b"video-frame" * 300000
            path.write_bytes(data)
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                self.assertEqual(linear_source.file_sha256(path), hashlib.sha256(data).hexdigest())

    def test_compatibility_entry_policies(self):
        for name, guarded, stride in (("comparison", False, False), ("guarded", True, False), ("stride", True, True)):
            module = importlib.import_module("run_linear_source_" + name)
            with self.subTest(name=name), patch.object(module, "run") as run:
                module.main(["--help"])
                run.assert_called_once_with(["--help"], guarded=guarded, allow_stride=stride)

    def test_launch_conditions_and_failed_state_are_preserved(self):
        for guarded, stride, step in ((False, False, 1), (True, False, 1), (True, True, 2)):
            with self.subTest(guarded=guarded, stride=stride), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                binary = root / "demos/hdmi_wall/build-grab-select/hdmi_wall.pcie"
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"test binary")
                source = root / "input.mp4"
                source.write_bytes(b"test source")
                args = ["--output", str(root / "result"), "--input", str(source), "--device", "2", "--streams", "8", "--duration", "5"]
                if stride:
                    args += ["--retrieve-every", str(step)]
                with patch.object(linear_source, "ROOT", root), patch.object(Path, "iterdir", return_value=iter(())), patch.object(linear_source.threading, "Thread"), patch.object(linear_source, "save") as save, patch.object(linear_source, "run_stage", side_effect=RuntimeError("test interruption")) as launch:
                    with self.assertRaisesRegex(RuntimeError, "test interruption"):
                        linear_source.main(args, guarded=guarded, allow_stride=stride)
                command = launch.call_args.args[2]
                for flag, value in (("--device", 2), ("--streams", 8), ("--retrieve-every", step), ("--warmup", 30), ("--duration", 5), ("--window", 5), ("--preview-fps", 10), ("--wall-fps", 10), ("--input", source)):
                    self.assertEqual(command[command.index(flag) + 1], value)
                self.assertIn("VPP_DIAG_OUTPUT_FORMAT=0", command)
                self.assertIn("VPP_DIAG_EXTRA_FRAMES=8", command)
                metadata = save.call_args_list[1].args[1]
                self.assertEqual(metadata["retrieve_every"], step)
                self.assertIn("input_sha256", metadata)
                self.assertEqual(save.call_args.args[1]["status"], "failed")

    def test_fixed_retrieval_entry_rejects_stride_option_before_hardware(self):
        with patch.object(Path, "iterdir") as scan:
            with self.assertRaises(SystemExit) as failure:
                linear_source.main(["--output", "unused", "--input", "unused", "--retrieve-every", "2"], allow_stride=False)
            self.assertEqual(failure.exception.code, 2)
            scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
