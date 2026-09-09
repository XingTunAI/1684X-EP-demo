import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "run.py"
spec = importlib.util.spec_from_file_location("_hdmi_wall_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LauncherTests(unittest.TestCase):
    def parse(self, *extra):
        return runner.arguments(["--root", "/board/repo", *extra])

    def command_value(self, plan, name):
        command = plan["worker_command"]
        return command[command.index(name) + 1]

    def test_default_latest_and_policy_specific_age_default(self):
        for policy, age in (("latest", 250.0), ("all", 0.0)):
            with self.subTest(policy=policy):
                args = self.parse(*(["--policy", policy] if policy == "all" else []))
                plan = runner.build_plan(args)
                self.assertEqual(args.policy, policy)
                self.assertEqual(plan["policy"], policy)
                self.assertEqual(plan["infer_fps"], 0.0)
                self.assertEqual(plan["max_frame_age_ms"], age)
                self.assertEqual(self.command_value(plan, "--policy"), policy)
                self.assertEqual(float(self.command_value(plan, "--infer-fps")), 0.0)
                self.assertEqual(float(self.command_value(plan, "--max-frame-age-ms")), age)

    def test_fractional_limits_and_explicit_age_disable(self):
        for age in ("125.5", "0"):
            with self.subTest(age=age):
                args = self.parse("--streams", "32", "--infer-fps", "7.5", "--max-frame-age-ms", age)
                plan = runner.build_plan(args)
                self.assertEqual(float(self.command_value(plan, "--infer-fps")), 7.5)
                self.assertEqual(float(self.command_value(plan, "--max-frame-age-ms")), float(age))
                self.assertEqual(self.command_value(plan, "--streams"), "32")

    def test_all_accepts_only_disabled_limits(self):
        args = self.parse("--policy", "all", "--infer-fps", "0", "--max-frame-age-ms", "0")
        self.assertEqual(args.infer_fps, 0.0)
        self.assertEqual(args.max_frame_age_ms, 0.0)
        for option in ("--infer-fps", "--max-frame-age-ms"):
            with self.subTest(option=option), patch("sys.stderr", new_callable=io.StringIO) as error:
                with self.assertRaises(SystemExit) as raised:
                    self.parse("--policy", "all", option, "0.1")
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(f"{option} must be 0 with --policy all", error.getvalue())

    def test_invalid_limits_rejected_before_launch(self):
        for option in ("--infer-fps", "--max-frame-age-ms"):
            for value in ("-1", "nan", "NaN", "inf", "-inf", "1e309", "no", ""):
                with self.subTest(option=option, value=value), patch("sys.stderr", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as raised:
                        self.parse(f"{option}={value}")
                    self.assertEqual(raised.exception.code, 2)

    def test_rtsp_uri_is_preserved_and_not_checked_as_a_local_file(self):
        for scheme in ("rtsp", "rtsps"):
            source = f"{scheme}://user:pass@example.invalid:8554/live/a?profile=1&transport=tcp"
            with self.subTest(scheme=scheme):
                args = self.parse("--input", source)
                plan = runner.build_plan(args)
                self.assertEqual(plan["input"], source)
                self.assertEqual(self.command_value(plan, "--input"), source)
                required = runner.required_files(args, plan)
                self.assertNotIn(source, required)
                self.assertIn(plan["model"], required)
                self.assertIn(plan["classnames"], required)
                self.assertIn(plan["worker_command"][0], required)
                self.assertIn(plan["player_command"][0], required)

    def test_distinct_rtsp_channels_require_the_cpp_inputs_file_entry(self):
        with patch("sys.stderr", new_callable=io.StringIO) as error:
            with self.assertRaises(SystemExit) as raised:
                self.parse("--input", "rtsp://camera.invalid/live", "--streams", "2")
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("--inputs-file", error.getvalue())

    def test_local_paths_and_optional_model_still_require_files(self):
        for source, expected in (("data/inputs/a clip.mp4", "/board/repo/data/inputs/a clip.mp4"),
                                 ("/videos/clip.mp4", "/videos/clip.mp4")):
            with self.subTest(source=source):
                args = self.parse("--input", source, "--score-gate", "on")
                plan = runner.build_plan(args)
                self.assertEqual(plan["input"], expected)
                self.assertEqual(self.command_value(plan, "--input"), expected)
                required = runner.required_files(args, plan)
                self.assertIn(expected, required)
                self.assertIn(plan["score_gate_model"], required)

    def test_cli_dry_run_prints_valid_plan_without_board_files(self):
        completed = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "--root", "/nonexistent/board/repo", "--dry-run",
             "--streams", "32", "--policy", "latest", "--infer-fps", "8"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        plan = json.loads(completed.stdout)
        self.assertEqual(plan["streams"], 32)
        self.assertEqual(plan["policy"], "latest")
        self.assertEqual(plan["infer_fps"], 8.0)
        self.assertEqual(plan["max_frame_age_ms"], 250.0)


class IdentityTests(unittest.TestCase):
    def fixture(self):
        command = ["/usr/bin/ffplay", "-i", "/result/preview.bgr"]
        process = Mock(pid=101, returncode=None)
        process.poll.side_effect = lambda: process.returncode
        identity = dict(pid=101, pgid=101, session=101, start_ticks="123", boot_id="boot",
                        exe=runner.os.path.realpath(command[0]), cmdline=command)
        fields = ["0"] * 20
        fields[0], fields[2], fields[3], fields[19] = "S", "101", "101", "123"
        return process, command, identity, "101 (ffplay) " + " ".join(fields)

    def test_process_identity_rejects_empty_or_changing_exec_metadata(self):
        process, command, identity, proc_stat = self.fixture()
        raw_command = b"\0".join(part.encode() for part in command) + b"\0"
        for readings in ([b""], [raw_command, b"/old/program\0"]):
            with self.subTest(readings=readings), \
                    patch.object(runner.Path, "read_text", return_value=proc_stat), \
                    patch.object(runner.Path, "read_bytes", side_effect=readings), \
                    patch.object(runner.os, "readlink", return_value=identity["exe"]):
                self.assertIsNone(runner.process_identity(process.pid))

    def test_capture_retries_empty_exec_argv_then_matches_complete_command(self):
        process, command, identity, proc_stat = self.fixture()
        raw_command = b"\0".join(part.encode() for part in command) + b"\0"
        with patch.object(runner.Path, "read_text", return_value=proc_stat), \
                patch.object(runner.Path, "read_bytes", side_effect=[b"", raw_command, raw_command]), \
                patch.object(runner.os, "readlink", return_value=identity["exe"]), \
                patch.object(runner, "boot_id", return_value="boot"), \
                patch.object(runner.time, "sleep") as sleep:
            self.assertEqual(runner.capture_child_identity(process, command), identity)
            sleep.assert_called_once()

    def test_capture_rejects_wrong_command_executable_or_session(self):
        process, command, identity, _ = self.fixture()
        for invalid in (dict(identity, cmdline=[]), dict(identity, cmdline=["/old/program"]),
                        dict(identity, exe="/old/program"), dict(identity, session=999)):
            with self.subTest(invalid=invalid), \
                    patch.object(runner, "process_identity", side_effect=[invalid, identity]), \
                    patch.object(runner.time, "sleep") as sleep:
                self.assertEqual(runner.capture_child_identity(process, command), identity)
                sleep.assert_called_once()

    def test_child_exit_during_capture_is_reaped_with_original_status(self):
        process, command, _, _ = self.fixture()
        process.returncode = 7
        with patch.object(runner, "process_identity") as capture:
            self.assertIsNone(runner.capture_child_identity(process, command))
            capture.assert_not_called()
        self.assertEqual(process.returncode, 7)

    def test_capture_timeout_cleans_owned_worker_and_saves_no_partial_identity(self):
        worker, command, _, _ = self.fixture()
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        def terminate():
            worker.returncode = -runner.signal.SIGTERM

        worker.terminate.side_effect = terminate
        worker.wait.side_effect = lambda timeout: worker.returncode
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test-run"
            plan = {"output": str(output), "worker_command": command,
                    "player_command": ["/usr/bin/player"], "player_environment": {}}
            args = runner.arguments(["--root", "/board/repo"])

            def launch(*_args, **_kwargs):
                output.mkdir()
                return worker

            with patch.dict(sys.modules, {"fcntl": Mock(LOCK_EX=1, LOCK_NB=2)}), \
                    patch.object(runner, "other_loads", return_value=[]), \
                    patch.object(runner, "required_files", return_value=[]), \
                    patch.object(runner, "device_snapshot", return_value=[]), \
                    patch.object(runner.os, "access", return_value=True), \
                    patch.object(runner, "process_identity", return_value=None), \
                    patch.object(runner.subprocess, "Popen", side_effect=launch), \
                    patch.object(runner.time, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(runner.time, "sleep", side_effect=sleep), \
                    patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(runner.run(args, plan), 1)
            metadata = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertIn("Timed out capturing complete process identity", metadata["error"])
            self.assertNotIn("worker", metadata)
            self.assertEqual(metadata["worker_returncode"], -runner.signal.SIGTERM)
        worker.terminate.assert_called_once()
        worker.wait.assert_called_once()
        self.assertLess(now[0], runner.IDENTITY_TIMEOUT + 0.1)

    def test_player_capture_failure_keeps_both_children_registered_for_cleanup(self):
        worker, command, identity, _ = self.fixture()
        player = Mock(pid=102, returncode=None)
        player.poll.side_effect = lambda: player.returncode
        events = []

        def finish_worker():
            self.assertIsNone(player.poll())
            events.append("worker")
            worker.returncode = 2

        def finish_player():
            self.assertEqual(worker.poll(), 2)
            events.append("player")
            player.returncode = 123

        worker.terminate.side_effect = finish_worker
        player.terminate.side_effect = finish_player
        worker.wait.side_effect = lambda timeout: worker.returncode
        player.wait.side_effect = lambda timeout: player.returncode
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test-run"
            plan = {"output": str(output), "worker_command": command,
                    "player_command": ["/usr/bin/player"], "player_environment": {}}
            args = runner.arguments(["--root", "/board/repo"])

            def launch(argv, **_kwargs):
                if argv == command:
                    output.mkdir()
                    (output / "preview.bgr").touch()
                    return worker
                return player

            replace = runner.os.replace

            def publish_file(source, destination):
                if Path(source).name == ".test-run.worker.log":
                    # Linux permits renaming the launcher's open log. Model
                    # that publication on Windows without moving an open file.
                    Path(destination).write_bytes(Path(source).read_bytes())
                else:
                    replace(source, destination)

            with patch.dict(sys.modules, {"fcntl": Mock(LOCK_EX=1, LOCK_NB=2)}), \
                    patch.object(runner, "other_loads", return_value=[]), \
                    patch.object(runner, "required_files", return_value=[]), \
                    patch.object(runner, "device_snapshot", return_value=[]), \
                    patch.object(runner.os, "access", return_value=True), \
                    patch.object(runner.os, "replace", side_effect=publish_file), \
                    patch.object(runner.stat, "S_ISFIFO", return_value=True), \
                    patch.object(runner, "process_identity", return_value=None), \
                    patch.object(runner, "send_verified", return_value=False), \
                    patch.object(runner, "capture_child_identity", side_effect=[identity, RuntimeError("player identity unavailable")]), \
                    patch.object(runner.subprocess, "Popen", side_effect=launch), \
                    patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(runner.run(args, plan), 1)
            metadata = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["error"], "player identity unavailable")
            self.assertEqual(metadata["worker"], identity)
            self.assertNotIn("player", metadata)
            self.assertEqual((metadata["worker_returncode"], metadata["player_returncode"]), (2, 123))
        self.assertEqual(events, ["worker", "player"])


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        # Exercise Linux shutdown behavior on Windows development hosts too.
        kill_signal = patch.object(runner.signal, "SIGKILL", 9, create=True)
        kill_signal.start()
        self.addCleanup(kill_signal.stop)

    def process(self, pid, returncode=None):
        process = Mock(pid=pid, returncode=returncode)
        process.poll.side_effect = lambda: process.returncode
        return process

    def test_worker_finishes_summary_with_player_connected(self):
        worker, player = self.process(101), self.process(102)
        events = []

        def send(identity, signum, group=False):
            events.append((identity["pid"], signum))
            if identity["pid"] == player.pid:
                self.assertEqual(events[-2], "summary written")
                self.assertEqual(worker.returncode, 2)
            return True

        def finish_worker(timeout):
            self.assertIsNone(player.poll(), "The FIFO reader must survive worker shutdown")
            self.assertNotIn((player.pid, runner.signal.SIGTERM), events)
            events.append("summary written")
            worker.returncode = 2  # C++ interrupted runs deliberately return 2.
            return worker.returncode

        def finish_player(timeout):
            player.returncode = 123  # Preserve ffplay's own TERM return code.
            return player.returncode

        worker.wait.side_effect = finish_worker
        player.wait.side_effect = finish_player
        with patch.object(runner, "send_verified", side_effect=send):
            runner.stop_children([(worker, {"pid": worker.pid}), (player, {"pid": player.pid})])
        self.assertEqual(events, [(101, runner.signal.SIGTERM), "summary written", (102, runner.signal.SIGTERM)])
        self.assertEqual((worker.returncode, player.returncode), (2, 123))

    def test_stuck_worker_is_killed_before_player_cleanup(self):
        worker, player = self.process(101), self.process(102)
        events = []

        def send(identity, signum, group=False):
            events.append((identity["pid"], signum))
            if identity["pid"] == worker.pid and signum == runner.signal.SIGKILL:
                worker.returncode = -runner.signal.SIGKILL
            if identity["pid"] == player.pid:
                self.assertIsNotNone(worker.poll())
                player.returncode = 0
            return True

        def wait_worker(timeout):
            if worker.returncode is None:
                raise subprocess.TimeoutExpired("worker", timeout)
            return worker.returncode

        worker.wait.side_effect = wait_worker
        player.wait.return_value = 0
        with patch.object(runner, "send_verified", side_effect=send):
            runner.stop_children([(worker, {"pid": worker.pid}), (player, {"pid": player.pid})])
        self.assertEqual(events, [(101, runner.signal.SIGTERM), (101, runner.signal.SIGKILL),
                                  (102, runner.signal.SIGTERM)])
        self.assertEqual([call.kwargs["timeout"] for call in worker.wait.call_args_list],
                         [runner.STOP_GRACE, runner.REAP_TIMEOUT])

    def test_natural_or_failed_peer_exit_is_not_resignalled_or_overwritten(self):
        for exited_pid in (101, 102):
            for code in (0, 7):
                with self.subTest(exited_pid=exited_pid, returncode=code):
                    worker, player = self.process(101), self.process(102)
                    exited = worker if exited_pid == worker.pid else player
                    live = player if exited is worker else worker
                    exited.returncode = code
                    exited.wait.return_value = code
                    live.wait.return_value = 0
                    with patch.object(runner, "send_verified", return_value=True) as send:
                        runner.stop_children([(worker, {"pid": worker.pid}), (player, {"pid": player.pid})])
                    send.assert_called_once_with({"pid": live.pid}, runner.signal.SIGTERM, group=True)
                    self.assertEqual(exited.poll(), code)

    def test_uncaptured_owned_child_is_killed_if_terminate_times_out(self):
        process = self.process(101)
        process.wait.side_effect = [subprocess.TimeoutExpired("child", runner.STOP_GRACE), -9]
        with patch.object(runner, "send_verified", return_value=False):
            runner.stop_children([(process, None)])
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual([call.kwargs["timeout"] for call in process.wait.call_args_list],
                         [runner.STOP_GRACE, runner.REAP_TIMEOUT])

    def stop_metadata(self, directory):
        parent = Path(directory)
        output = parent / "test-run"
        output.mkdir()
        (parent / "latest.json").write_text(json.dumps({"output": str(output)}), encoding="utf-8")
        identities = {name: {"pid": pid} for name, pid in (("launcher", 100), ("worker", 101), ("player", 102))}
        (output / "run.json").write_text(json.dumps(dict(identities, launcher_kind="bm1684x-hdmi-wall-v1")), encoding="utf-8")
        return parent, identities

    def test_stop_latest_waits_for_both_launcher_cleanup_phases(self):
        with tempfile.TemporaryDirectory() as directory:
            parent, identities = self.stop_metadata(directory)
            now = [0.0]
            events = []
            # Exceeds the old nine-second wait but fits sequential child cleanup.
            completed_at = runner.STOP_GRACE + runner.REAP_TIMEOUT + 3

            def alive(identity):
                return identity is not None and now[0] < completed_at

            def send(identity, signum, group=False):
                if not alive(identity):
                    return False
                events.append((identity["pid"], signum))
                self.assertEqual(identity, identities["launcher"], "Do not interrupt the launcher's ordered cleanup")
                return True

            def sleep(seconds):
                now[0] += seconds

            with patch.object(runner, "send_verified", side_effect=send), \
                    patch.object(runner, "is_same_process", side_effect=alive), \
                    patch.object(runner.time, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(runner.time, "sleep", side_effect=sleep), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(runner.stop_latest(parent), 0)
            self.assertEqual(events, [(100, runner.signal.SIGTERM)])

    def test_stop_latest_without_launcher_stops_worker_before_player(self):
        with tempfile.TemporaryDirectory() as directory:
            parent, identities = self.stop_metadata(directory)
            now = [0.0]
            deadlines = {101: float("inf"), 102: float("inf")}
            events = []

            def alive(identity):
                return identity is not None and now[0] < deadlines.get(identity["pid"], 0)

            def send(identity, signum, group=False):
                if not alive(identity):
                    return False
                pid = identity["pid"]
                events.append((pid, signum))
                if pid == 102:
                    self.assertFalse(alive(identities["worker"]), "Fallback must keep the FIFO reader until worker exit")
                deadlines[pid] = now[0] + 0.5
                return True

            def sleep(seconds):
                now[0] += seconds

            with patch.object(runner, "send_verified", side_effect=send), \
                    patch.object(runner, "is_same_process", side_effect=alive), \
                    patch.object(runner.time, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(runner.time, "sleep", side_effect=sleep), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(runner.stop_latest(parent), 0)
            self.assertEqual(events, [(101, runner.signal.SIGTERM), (102, runner.signal.SIGTERM)])


if __name__ == "__main__":
    unittest.main()
