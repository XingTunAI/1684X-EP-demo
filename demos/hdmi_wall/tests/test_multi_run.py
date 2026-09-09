import contextlib
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "multi_run.py"
with patch.object(sys, "path", [str(RUNNER_PATH.parent), *sys.path]):
    spec = importlib.util.spec_from_file_location("_hdmi_multi_runner", RUNNER_PATH)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)


class PlanTests(unittest.TestCase):
    def parse(self, *extra):
        return runner.arguments(["--root", "/board/repo", *extra])

    def test_four_devices_get_independent_32_channel_processes_and_outputs(self):
        args = self.parse("--devices", "0,1,2,3")
        plan = runner.build_plan(args)
        self.assertEqual(plan["total_streams"], 128)
        self.assertEqual(plan["shared_lock"], "/board/repo/data/results/hdmi-wall/launcher.lock")
        self.assertEqual(len(plan["viewer_manifest"]["devices"]), 4)
        self.assertIs(plan["viewer_manifest"]["supervised_close"], True)
        for index, worker in enumerate(plan["workers"]):
            command = worker["worker_command"]
            self.assertEqual(command[command.index("--device") + 1], str(index))
            self.assertEqual(command[command.index("--streams") + 1], "32")
            self.assertEqual(command[-1], worker["output"])
            self.assertEqual(worker["output"], plan["output"] + f"/device_{index}")
            self.assertEqual(worker["fifo"], worker["output"] + "/preview.bgr")
            self.assertEqual(worker["model"].split("/")[-1], "yolov8s_int8_1b.bmodel")
            self.assertEqual(worker["infer_fps"], 5)

    def test_arbitrary_device_order_and_stream_count_are_preserved(self):
        plan = runner.build_plan(self.parse("--devices", "3, 1", "--streams", "7", "--model", "n", "--score-gate", "on"))
        self.assertEqual(plan["total_streams"], 14)
        self.assertEqual([item["device"] for item in plan["workers"]], [3, 1])
        for worker in plan["workers"]:
            self.assertIn("--score-gate-model", worker["worker_command"])
            self.assertIn(worker["score_gate_model"], runner.required_files(self.parse("--score-gate", "on"), plan))

    def test_policy_defaults_and_fractional_limits(self):
        args = self.parse("--policy", "all")
        self.assertEqual((args.infer_fps, args.max_frame_age_ms), (0, 0))
        args = self.parse("--infer-fps", "2.5", "--max-frame-age-ms", "0")
        self.assertEqual((args.infer_fps, args.max_frame_age_ms), (2.5, 0))

    def test_invalid_limits_devices_and_combinations_are_rejected(self):
        invalid = [
            ("--devices", value) for value in ("", "-1", "0,0", "0,1,2,3,4", "0,,1", "a", "1.5")
        ] + [
            ("--streams", "33"), ("--streams", "0"),
            ("--infer-fps", "nan"), ("--max-frame-age-ms", "inf"),
            ("--infer-fps=-1",), ("--policy", "all", "--infer-fps", "5"),
            ("--policy", "all", "--max-frame-age-ms", "250"), ("--stop", "--dry-run"),
        ]
        for options in invalid:
            with self.subTest(options=options), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    self.parse(*options)

    def test_rtsp_cannot_be_repeated_across_devices_or_streams(self):
        uri = "rtsp://example.invalid/live?profile=1"
        for options in (("--devices", "0,1", "--streams", "1"), ("--devices", "0", "--streams", "2")):
            with self.subTest(options=options), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    self.parse("--input", uri, *options)
        args = self.parse("--devices", "2", "--streams", "1", "--input", uri)
        plan = runner.build_plan(args)
        self.assertEqual(plan["workers"][0]["input"], uri)
        self.assertNotIn(uri, runner.required_files(args, plan))

    def test_viewer_uses_system_libraries_and_no_ffplay_preflight(self):
        with patch.dict(runner.os.environ, {"LD_LIBRARY_PATH": "/opt/vendor/lib"}, clear=True):
            args = self.parse()
            plan = runner.build_plan(args)
        self.assertEqual(plan["viewer_environment"]["LD_LIBRARY_PATH"], "/usr/lib/aarch64-linux-gnu")
        required = runner.required_files(args, plan)
        self.assertIn("/board/repo/demos/hdmi_wall/viewer.py", required)
        self.assertNotIn("/usr/bin/ffplay", required)
        self.assertEqual(len(required), len(set(required)))

    def test_cli_dry_run_requires_no_board_files(self):
        completed = subprocess.run([sys.executable, str(RUNNER_PATH), "--root", "/missing/repo",
                                    "--devices", "0,1,2,3", "--dry-run"],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["total_streams"], 128)


class SupervisionTests(unittest.TestCase):
    def run_fixture(self, codes, viewer_codes=None, capture_fail_at=None, signal_at_tick=None, timeout=False,
                    fifo_ready=False, close_at_tick=None, close_pid=None):
        """Fake child lifetimes while exercising real metadata and cleanup flow."""
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            args = runner.arguments(["--root", "/board/repo", "--streams", "1", "--fifo-timeout", "1"])
            args.root = directory
            plan = runner.build_plan(args)
            events = []
            process_list = []
            tick = [0]
            handlers = {}

            class Child:
                def __init__(self, role, sequence):
                    self.pid = 100 + len(process_list)
                    self.role = role
                    self.sequence = sequence
                    self.returncode = None

                def poll(self):
                    if self.returncode is None:
                        self.returncode = self.sequence[min(tick[0], len(self.sequence) - 1)]
                    return self.returncode

            def popen(command, **kwargs):
                self.assertTrue(kwargs["start_new_session"])
                if "--manifest" in command:
                    role, sequence = "viewer", viewer_codes or [None] * 10
                else:
                    device = int(command[command.index("--device") + 1])
                    role, sequence = f"worker{device}", codes[device]
                events.append(("start", role))
                process = Child(role, sequence)
                process_list.append(process)
                return process

            def capture(process, command):
                if len(process_list) == capture_fail_at:
                    raise RuntimeError("exec identity capture failed")
                return {"pid": process.pid}

            def cleanup(children):
                for child, _identity in children:
                    if close_at_tick is not None and child.role.startswith("worker"):
                        self.assertIsNone(process_list[0].poll(), "viewer exited before worker drain")
                    events.append(("cleanup", child.role, tick[0]))
                    if child.poll() is None:
                        child.returncode = -signal.SIGTERM

            def sleep(_seconds):
                tick[0] += 1
                if signal_at_tick == tick[0]:
                    handlers[signal.SIGTERM](signal.SIGTERM, None)
                if close_at_tick == tick[0]:
                    (Path(plan["output"]) / "viewer-status.json").write_text(json.dumps({
                        "pid": process_list[0].pid if close_pid is None else close_pid,
                        "close_requested": True,
                    }))
                self.assertLess(tick[0], 20, "supervisor did not terminate")

            def install_handler(signum, handler):
                previous = handlers.get(signum)
                handlers[signum] = handler
                return previous

            fcntl = types.SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
            stack.enter_context(patch.dict(sys.modules, {"fcntl": fcntl}))
            stack.enter_context(patch.object(runner, "required_files", return_value=[]))
            stack.enter_context(patch.object(runner.os, "access", return_value=True))
            stack.enter_context(patch.object(runner.single, "other_loads", return_value=[]))
            stack.enter_context(patch.object(runner.single, "device_snapshot", return_value=[]))
            stack.enter_context(patch.object(runner.single, "process_identity", return_value={"pid": 99}))
            stack.enter_context(patch.object(runner.single, "capture_child_identity", side_effect=capture))
            stack.enter_context(patch.object(runner.single, "stop_children", side_effect=cleanup))
            stack.enter_context(patch.object(runner.subprocess, "Popen", side_effect=popen))
            stack.enter_context(patch.object(runner.signal, "signal", side_effect=install_handler))
            stack.enter_context(patch.object(runner.time, "sleep", side_effect=sleep))
            stack.enter_context(patch.object(runner.time, "monotonic", side_effect=lambda: tick[0] * (1 if timeout else 0.01)))
            if fifo_ready:
                original_stat = runner.Path.stat

                def file_stat(path, *positional, **kwargs):
                    if path.name == "preview.bgr":
                        return types.SimpleNamespace(st_mode=runner.stat.S_IFIFO)
                    return original_stat(path, *positional, **kwargs)

                stack.enter_context(patch.object(runner.Path, "stat", new=file_stat))
            stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
            stack.enter_context(patch("sys.stderr", new_callable=io.StringIO))
            result = runner.run(args, plan)
            metadata = json.loads((Path(plan["output"]) / "run.json").read_text())
            manifest = json.loads((Path(plan["output"]) / "viewer.json").read_text())
            return result, metadata, manifest, events

    def test_viewer_starts_first_and_one_completed_device_does_not_stop_its_peer(self):
        result, metadata, manifest, events = self.run_fixture({0: [0], 1: [None, None, 0]})
        self.assertEqual(result, 0)
        self.assertEqual(events[:3], [("start", "viewer"), ("start", "worker0"), ("start", "worker1")])
        self.assertEqual(events[3:], [("cleanup", "worker0", 2), ("cleanup", "worker1", 2), ("cleanup", "viewer", 2)])
        self.assertEqual([page["status"] for page in manifest["devices"]], ["completed", "completed"])
        self.assertEqual(metadata["launcher_kind"], runner.LAUNCHER_KIND)

    def test_failed_device_keeps_other_device_running_but_fails_whole_run(self):
        result, metadata, _manifest, events = self.run_fixture({0: [7], 1: [None, None, 0]})
        self.assertEqual(result, 1)
        self.assertEqual([item["returncode"] for item in metadata["workers"]], [7, 0])
        self.assertTrue(all(event[2] == 2 for event in events if event[0] == "cleanup"))

    def test_closing_viewer_stops_all_workers(self):
        result, metadata, _manifest, events = self.run_fixture({0: [None] * 5, 1: [None] * 5}, [None, 0])
        self.assertEqual(result, 0)
        self.assertIn("viewer exited", metadata["exit_reason"])
        self.assertEqual([event[1] for event in events if event[0] == "cleanup"], ["worker0", "worker1", "viewer"])

    def test_supervised_close_keeps_viewer_alive_until_all_workers_stop(self):
        result, metadata, _manifest, events = self.run_fixture(
            {0: [None] * 5, 1: [None] * 5}, close_at_tick=1, fifo_ready=True)
        self.assertEqual(result, 0)
        self.assertEqual(metadata["exit_reason"], "viewer requested close")
        self.assertEqual([event for event in events if event[0] == "cleanup"], [
            ("cleanup", "worker0", 1), ("cleanup", "worker1", 1), ("cleanup", "viewer", 1)])

    def test_supervised_close_preserves_already_observed_worker_failure(self):
        result, metadata, _manifest, _events = self.run_fixture(
            {0: [7], 1: [None] * 5}, close_at_tick=1)
        self.assertEqual(result, 1)
        self.assertEqual(metadata["exit_reason"], "viewer requested close")
        self.assertEqual(metadata["workers"][0]["returncode"], 7)

    def test_other_viewer_pid_cannot_request_early_shutdown(self):
        result, metadata, _manifest, events = self.run_fixture(
            {0: [None, None, 0], 1: [None, None, 0]}, close_at_tick=1, close_pid=999)
        self.assertEqual(result, 0)
        self.assertEqual(metadata["exit_reason"], "all workers exited")
        self.assertTrue(all(event[2] == 2 for event in events if event[0] == "cleanup"))

    def test_viewer_startup_failure_launches_no_accelerator_work(self):
        result, metadata, _manifest, events = self.run_fixture({0: [None], 1: [None]}, [3])
        self.assertEqual(result, 3)
        self.assertEqual([event for event in events if event[0] == "start"], [("start", "viewer")])
        self.assertEqual([record["status"] for record in metadata["workers"]], ["not_started", "not_started"])

    def test_ready_fifos_disable_startup_deadline_while_workers_continue(self):
        result, metadata, _manifest, events = self.run_fixture(
            {0: [None, None, None, 0], 1: [None, None, None, 0]}, timeout=True, fifo_ready=True)
        self.assertEqual(result, 0)
        self.assertTrue(all("error" not in record for record in metadata["workers"]))
        self.assertTrue(all(event[2] == 3 for event in events if event[0] == "cleanup"))

    def test_simultaneous_viewer_failure_is_not_hidden_by_successful_workers(self):
        result, metadata, _manifest, _events = self.run_fixture({0: [None, 0], 1: [None, 0]}, [None, 4])
        self.assertEqual(result, 4)
        self.assertEqual(metadata["viewer_returncode"], 4)

    def test_identity_capture_failure_still_cleans_up_owned_worker_before_viewer(self):
        result, metadata, _manifest, events = self.run_fixture({0: [None], 1: [None]}, capture_fail_at=2)
        self.assertEqual(result, 1)
        self.assertIn("identity capture failed", metadata["error"])
        self.assertEqual([event[1] for event in events if event[0] == "cleanup"], ["worker0", "viewer"])
        self.assertEqual(metadata["workers"][1]["status"], "not_started")

    def test_term_stops_every_device_and_keeps_original_signal_status(self):
        result, metadata, _manifest, events = self.run_fixture({0: [None] * 5, 1: [None] * 5}, signal_at_tick=1)
        self.assertEqual(result, 128 + signal.SIGTERM)
        self.assertIn("received signal", metadata["exit_reason"])
        self.assertEqual([event[1] for event in events if event[0] == "cleanup"], ["worker0", "worker1", "viewer"])

    def test_fifo_timeout_is_recorded_and_bounded(self):
        result, metadata, _manifest, _events = self.run_fixture({0: [None] * 10, 1: [None] * 10}, timeout=True)
        self.assertEqual(result, 1)
        for record in metadata["workers"]:
            self.assertEqual(record["status"], "failed")
            self.assertIn("Timed out waiting for FIFO", record["error"])


class StopTests(unittest.TestCase):
    def test_close_request_ignores_missing_malformed_and_nonboolean_status(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertFalse(runner.viewer_close_requested(output, 101))
            for raw in ('{', '[]', 'null', '{"pid":101,"close_requested":1}',
                        '{"pid":"101","close_requested":true}', '{"pid":102,"close_requested":true}'):
                with self.subTest(raw=raw):
                    (output / "viewer-status.json").write_text(raw)
                    self.assertFalse(runner.viewer_close_requested(output, 101))
            (output / "viewer-status.json").write_text('{"pid":101,"close_requested":true}')
            self.assertTrue(runner.viewer_close_requested(output, 101))

    def test_stop_rejects_unrelated_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "multi"
            parent.mkdir()
            (parent / "latest.json").write_text(json.dumps({"output": str(Path(directory) / "other")}))
            with self.assertRaisesRegex(RuntimeError, "outside"):
                runner.stop_latest(parent)

    def test_stop_rejects_single_device_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            output = parent / "run"
            output.mkdir()
            (parent / "latest.json").write_text(json.dumps({"output": str(output)}))
            (output / "run.json").write_text(json.dumps({"launcher_kind": "bm1684x-hdmi-wall-v1"}))
            with self.assertRaisesRegex(RuntimeError, "Unrecognized"):
                runner.stop_latest(parent)

    def test_fallback_signals_verified_workers_before_viewer(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            output = parent / "run"
            output.mkdir()
            (parent / "latest.json").write_text(json.dumps({"output": str(output)}))
            metadata = {"launcher_kind": runner.LAUNCHER_KIND, "launcher": {"pid": 99},
                        "workers": [{"identity": {"pid": 100}}, {"identity": {"pid": 101}}],
                        "viewer": {"pid": 102}}
            (output / "run.json").write_text(json.dumps(metadata))
            with patch.object(runner.single, "send_verified", return_value=False) as send, \
                    patch.object(runner.single, "is_same_process", return_value=False), \
                    patch.object(runner.signal, "SIGKILL", 9, create=True), \
                    patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(runner.stop_latest(parent), 0)
            self.assertEqual([call.args[0]["pid"] for call in send.call_args_list], [99, 100, 100, 101, 101, 102, 102])
            for call in send.call_args_list[1:]:
                self.assertTrue(call.kwargs["group"])


if __name__ == "__main__":
    unittest.main()
