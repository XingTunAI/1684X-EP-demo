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

    def test_gate_merge_budget_is_forwarded_to_every_device_and_metadata(self):
        self.assertEqual(runner.build_plan(self.parse())["gate_merge_budget_kib"], 0)
        for budget in (0, 128, 1024):
            with self.subTest(budget=budget):
                plan = runner.build_plan(self.parse("--devices", "0,1,2,3", "--score-gate", "on",
                                                    "--gate-merge-budget-kib", str(budget)))
                self.assertEqual(plan["gate_merge_budget_kib"], budget)
                for worker in plan["workers"]:
                    command = worker["worker_command"]
                    self.assertEqual(worker["gate_merge_budget_kib"], budget)
                    self.assertEqual(command[command.index("--gate-merge-budget-kib") + 1], str(budget))
                    self.assertEqual(command[-2:], ["--output", worker["output"]])

    def test_policy_defaults_and_fractional_limits(self):
        args = self.parse("--policy", "all")
        self.assertEqual((args.infer_fps, args.max_frame_age_ms), (0, 0))
        args = self.parse("--infer-fps", "2.5", "--max-frame-age-ms", "0")
        self.assertEqual((args.infer_fps, args.max_frame_age_ms), (2.5, 0))

    def test_observation_validates_actual_card_stream_counts_and_forwards_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('{"devices":{"0":{"streams":2},"1":{"streams":32}}}', encoding="utf-8")
            with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                self.parse("--observe-decode", "on", "--compare-streams", "0,2", "--device-config", str(config))
            args = self.parse("--observe-decode", "on", "--compare-streams", "1,0", "--inference", "off",
                              "--observe-preview-fps", "10", "--device-config", str(config))
            plan = runner.build_plan(args)
        self.assertEqual(plan["inference"], "off")
        self.assertEqual(plan["compare_stream_ids"], [1, 0])
        for worker in plan["workers"]:
            command = worker["worker_command"]
            self.assertEqual(command[command.index("--observe-decode") + 1], "on")
            self.assertEqual(command[command.index("--inference") + 1], "off")
            self.assertEqual(command[command.index("--compare-streams") + 1], "1,0")
            self.assertEqual(command[command.index("--observe-preview-fps") + 1], "10.0")
            self.assertEqual(command[-2:], ["--output", worker["output"]])
            self.assertNotIn(worker["model"], runner.required_files(args, plan))
            self.assertNotIn(worker["classnames"], runner.required_files(args, plan))
        self.assertEqual(runner.build_plan(self.parse())["observe_decode"], "off")

    def test_heterogeneous_four_device_configuration_and_summary_metadata(self):
        options = {"0": {"streams": 20, "gate_merge_budget_kib": 64},
                   "1": {"streams": 32, "gate_merge_budget_kib": 128},
                   "2": {"streams": 12}, "3": {"gate_merge_budget_kib": 256}}
        context = {"pcie_devices": [{"device": 0, "label": "PCIe 2.0 x1"}]}
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "card settings.json"
            config.write_text(json.dumps({"devices": options, "context": context}), encoding="utf-8")
            plan = runner.build_plan(self.parse("--devices", "3,2,1,0", "--streams", "30",
                                                "--score-gate", "on", "--gate-merge-budget-kib", "8",
                                                "--device-config", str(config), "--record-mode", "summary"))
        self.assertEqual(plan["total_streams"], 94)
        self.assertIsNone(plan["streams_per_device"])
        self.assertIsNone(plan["gate_merge_budget_kib"])
        self.assertEqual(plan["device_configuration_context"], context)
        self.assertEqual(plan["streams_by_device"], {"0": 20, "1": 32, "2": 12, "3": 30})
        self.assertEqual(plan["gate_merge_budget_kib_by_device"], {"0": 64, "1": 128, "2": 8, "3": 256})
        for worker, page in zip(plan["workers"], plan["viewer_manifest"]["devices"]):
            command = worker["worker_command"]
            self.assertEqual(page["streams"], worker["streams"])
            self.assertEqual(command[command.index("--streams") + 1], str(worker["streams"]))
            self.assertEqual(command[command.index("--gate-merge-budget-kib") + 1], str(worker["gate_merge_budget_kib"]))
            self.assertEqual(command[command.index("--record-mode") + 1], "summary")
            self.assertEqual(command[-2:], ["--output", worker["output"]])

    def test_bad_device_configuration_is_rejected_before_launch(self):
        invalid = [{"devices": []}, {"devices": {"01": {"streams": 1}}},
                   {"devices": {"0": {"streams": True}}}, {"devices": {"0": {"streams": 33}}},
                   {"devices": {"0": {"gate_merge_budget_kib": -1}}},
                   {"devices": {"0": {"gate_merge_budget_kib": 1025}}},
                   {"devices": {"0": {"model": "n"}}}, {"devices": {"4": {"streams": 1}}},
                   {"devices": {}, "context": []}, {"devices": {}, "unexpected": 1}]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            for document in invalid:
                with self.subTest(document=document), patch("sys.stderr", new_callable=io.StringIO):
                    config.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(SystemExit):
                        self.parse("--score-gate", "on", "--device-config", str(config))
            config.write_text(json.dumps({"devices": {"0": {"gate_merge_budget_kib": 64}}}), encoding="utf-8")
            with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                self.parse("--device-config", str(config))
            config.write_text(json.dumps({"devices": {"0": {"streams": 2}}}), encoding="utf-8")
            with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                self.parse("--devices", "0", "--streams", "1", "--input", "rtsp://camera/live", "--device-config", str(config))

    def test_viewer_links_follow_actual_device_ids_and_require_available_observation(self):
        args = self.parse("--devices", "7,0,1", "--score-gate", "on")
        args.device_overrides = {"7": {"streams": 20}, "0": {"streams": 30}}
        args.device_configuration_context = {"pcie_devices": [
            {"device": 0, "available": True, "label": "PCIe 3.0 x2"},
            {"device": 1, "available": False, "label": "PCIe 2.0 x1"},
            {"device": 7, "available": True, "label": "PCIe 2.0 x1"},
        ]}
        pages = runner.build_plan(args)["viewer_manifest"]["devices"]
        self.assertEqual([(page["device"], page["streams"], page.get("pcie_link_label")) for page in pages],
                         [(7, 20, "PCIe 2.0 x1"), (0, 30, "PCIe 3.0 x2"), (1, 32, None)])
        args.device_configuration_context = {}
        self.assertTrue(all("pcie_link_label" not in page for page in runner.build_plan(args)["viewer_manifest"]["devices"]))

    def test_ambiguous_or_malformed_pcie_context_is_not_presented_as_a_link(self):
        self.assertEqual(runner.viewer_pcie_labels({"pcie_devices": "unknown"}), {})
        self.assertEqual(runner.viewer_pcie_labels({"pcie_devices": [
            {"device": 0, "available": True, "label": "PCIe 3.0 x2"},
            {"device": 0, "available": True, "label": "PCIe 2.0 x1"},
            {"device": 1, "available": True, "label": "PCIe guessed from device ID"},
            {"device": True, "available": True, "label": "PCIe 2.0 x1"},
            {"device": 2, "label": "PCIe 2.0 x1"}, None]}), {})

    def test_stop_does_not_read_device_config_and_telemetry_defaults_off(self):
        args = self.parse("--stop", "--device-config", "/missing/config.json")
        self.assertEqual(args.device_overrides, {})
        self.assertEqual(runner.build_plan(args)["telemetry_interval_seconds"], 0)
        self.assertEqual(runner.build_plan(self.parse("--telemetry-interval", "5"))["telemetry_interval_seconds"], 5)
        for value in ("-1", "nan", "inf"):
            with self.subTest(value=value), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                self.parse("--telemetry-interval=" + value)

    def test_invalid_limits_devices_and_combinations_are_rejected(self):
        invalid = [
            ("--devices", value) for value in ("", "-1", "0,0", "0,1,2,3,4", "0,,1", "a", "1.5")
        ] + [
            ("--streams", "33"), ("--streams", "0"),
            ("--infer-fps", "nan"), ("--max-frame-age-ms", "inf"),
            ("--infer-fps=-1",), ("--policy", "all", "--infer-fps", "5"),
            ("--gate-merge-budget-kib", "1"),
            ("--score-gate", "on", "--gate-merge-budget-kib", "1025"),
            ("--score-gate", "on", "--gate-merge-budget-kib", "1.5"),
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


class TelemetryTests(unittest.TestCase):
    def test_sudo_path_falls_back_to_sdk_bm_smi_and_records_valid_utilization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = {"device": 1, "output": directory, "status": "running", "streams": 32}
            with patch.object(runner.shutil, "which", return_value=None), \
                    patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "TPU 98%", "")) as query:
                sampler = runner.Telemetry(root, root, [worker], 5)
                sampler.tick()
                summary = sampler.close()
            self.assertEqual(query.call_args.args[0][0], "/opt/sophon/libsophon-current/bin/bm-smi")
            self.assertEqual(summary["devices"]["1"]["mean_percent"], 98)

    def test_formal_load_uses_each_worker_window_and_labels_incomplete_or_missing_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workers = [{"device": device, "output": str(root / f"device_{device}"),
                        "status": "completed", "streams": 32} for device in range(3)]
            for device, start, end, status in ((0, 1, 5, "measured"), (1, 2, 4, "interrupted")):
                output = Path(workers[device]["output"])
                output.mkdir()
                (output / "summary.json").write_text(json.dumps({"measurement_start_monotonic_s": start,
                    "observed_measurement_end_monotonic_s": end, "status": status,
                    "record_mode": "summary", "accounting_complete": True}), encoding="utf-8")
            sampler = runner.Telemetry(root, root, workers, 5)
            for device, timestamp, value in ((0, 0, 1), (0, 1, 90), (0, 3, None), (0, 5, 0),
                                             (1, 1, 0), (1, 2, 100), (1, 4, 0)):
                sampler.handle.write(json.dumps({"device": device, "monotonic_s": timestamp,
                                                 "tpu_util_percent": value}) + "\n")
            sampler.handle.write('{"incomplete_tail":')
            formal = sampler.close()["formal_measurement"]
            self.assertEqual(formal["malformed_rows"], 1)
            self.assertIsNone(formal["read_error"])
            self.assertEqual(formal["devices"]["0"]["mean_percent"], 90)
            self.assertEqual(formal["devices"]["0"]["samples"], 2)
            self.assertEqual(formal["devices"]["0"]["read_failures"], 1)
            self.assertEqual(formal["devices"]["1"]["mean_percent"], 100)
            self.assertEqual(formal["devices"]["1"]["worker_summary_status"], "interrupted")
            self.assertIsNone(formal["devices"]["2"]["mean_percent"])
            self.assertTrue(formal["devices"]["2"]["error"])

    def test_four_cards_are_staggered_with_at_most_one_bounded_query_per_tick(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workers = [{"device": device, "output": directory, "status": "running", "streams": 32}
                       for device in (0, 1, 2, 3)]
            (root / "status.json").write_text(json.dumps({"timestamp_unix_ms": 123, "streams": [
                {"has_image": True, "stale": False}, {"has_image": False, "stale": True}]}), encoding="utf-8")
            now = [0.0]
            with patch.object(runner.time, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "TPU 96% Memory 30%", "")) as query:
                sampler = runner.Telemetry(root, root, workers, 5)
                for index in range(4):
                    now[0] = index * 1.25
                    sampler.tick()
                    self.assertEqual(query.call_count, index + 1)
                    sampler.tick()
                    self.assertEqual(query.call_count, index + 1)
                summary = sampler.close()
            rows = [json.loads(line) for line in (root / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["device"] for row in rows], [0, 1, 2, 3])
            self.assertEqual([row["tpu_util_percent"] for row in rows], [96] * 4)
            self.assertEqual(rows[0]["wall"]["has_image"], 1)
            self.assertEqual(rows[0]["wall"]["stale"], 1)
            self.assertEqual(rows[0]["wall"]["timestamp_unix_ms"], 123)
            self.assertGreater(rows[0]["disk_free_bytes"], 0)
            for index, call in enumerate(query.call_args_list):
                self.assertEqual(call.args[0][1:3], [f"--start_dev={index}", f"--last_dev={index}"])
                self.assertEqual(call.kwargs["timeout"], 2)
                self.assertEqual(summary["devices"][str(index)]["mean_percent"], 96)

    def test_failed_queries_are_null_and_excluded_from_mean_without_catchup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = {"device": 1, "output": directory, "status": "starting", "streams": 30}
            now = [0.0]
            responses = [subprocess.CompletedProcess([], 0, "95%", ""),
                         subprocess.TimeoutExpired("bm-smi", 2), subprocess.CompletedProcess([], 0, "unavailable", ""),
                         subprocess.CompletedProcess([], 0, "0%", "")]
            with patch.object(runner.time, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(runner.subprocess, "run", side_effect=responses) as query:
                sampler = runner.Telemetry(root, root, [worker], 5)
                for timestamp in (0, 5, 50, 55):
                    now[0] = timestamp
                    sampler.tick()
                    sampler.tick()
                summary = sampler.close()
                self.assertEqual(query.call_count, 4)
            rows = [json.loads(line) for line in (root / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["tpu_util_percent"] for row in rows], [95, None, None, 0])
            self.assertTrue(rows[1]["error"])
            self.assertIsNone(rows[0]["wall"]["has_image"])
            self.assertEqual(summary["devices"]["1"], {"samples": 4, "valid_samples": 2, "read_failures": 2,
                                                       "sum_percent": 95, "min_percent": 0, "max_percent": 95,
                                                       "mean_percent": 47.5})

    def test_invalid_first_percentage_is_never_reinterpreted_as_a_valid_later_value(self):
        outputs = ("TPU -1% Memory 90%", "TPU N/A% Memory 90%", "TPU 1e2% Memory 90%", "TPU 101%")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = {"device": 0, "output": directory, "status": "running", "streams": 32}
            now = [0.0]
            responses = [subprocess.CompletedProcess([], 0, value, "") for value in outputs]
            with patch.object(runner.time, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(runner.subprocess, "run", side_effect=responses):
                sampler = runner.Telemetry(root, root, [worker], 5)
                for index in range(len(outputs)):
                    now[0] = index * 5
                    sampler.tick()
                summary = sampler.close()
            self.assertEqual(summary["devices"]["0"]["read_failures"], len(outputs))
            self.assertIsNone(summary["devices"]["0"]["mean_percent"])


class SupervisionTests(unittest.TestCase):
    def run_fixture(self, codes, viewer_codes=None, capture_fail_at=None, signal_at_tick=None, timeout=False,
                    fifo_ready=False, close_at_tick=None, close_pid=None, telemetry=None, telemetry_save_failure=False):
        """Fake child lifetimes while exercising real metadata and cleanup flow."""
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            args = runner.arguments(["--root", "/board/repo", "--streams", "1", "--fifo-timeout", "1"])
            args.telemetry_interval = 5 if telemetry is not None else 0
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
            if telemetry is not None:
                stack.enter_context(patch.object(runner, "Telemetry", return_value=telemetry))
            if telemetry_save_failure:
                save_json = runner.single.save_json
                def save_metadata(path, value):
                    if path.name == "telemetry-summary.json":
                        raise OSError("telemetry attachment cannot be written")
                    save_json(path, value)
                stack.enter_context(patch.object(runner.single, "save_json", side_effect=save_metadata))
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

    def test_telemetry_write_failure_keeps_final_metadata_and_worker_cleanup(self):
        for attachment_error in (False, True):
            with self.subTest(attachment_error=attachment_error):
                telemetry = Mock()
                telemetry.close.return_value = {"write_error": None if attachment_error else "disk write error"}
                result, metadata, _manifest, events = self.run_fixture(
                    {0: [None, 0], 1: [None, 0]}, telemetry=telemetry, telemetry_save_failure=attachment_error)
                self.assertEqual(result, 1)
                self.assertEqual(metadata["exit_status"], 1)
                self.assertEqual(metadata["status"], "stopped")
                self.assertEqual([worker["returncode"] for worker in metadata["workers"]], [0, 0])
                self.assertEqual([event[1] for event in events if event[0] == "cleanup"], ["worker0", "worker1", "viewer"])
                self.assertEqual(telemetry.close.call_count, 1)
                if attachment_error:
                    self.assertIn("attachment cannot be written", metadata["telemetry_error"])


class StopTests(unittest.TestCase):
    def test_cli_stop_never_builds_a_display_or_worker_launch_plan(self):
        args = runner.arguments(["--root", "/board/repo", "--stop"])
        with patch.object(runner, "arguments", return_value=args), \
                patch.object(runner.sys, "platform", "linux"), \
                patch.object(runner, "build_plan", side_effect=AssertionError("display/worker plan")), \
                patch.object(runner, "stop_latest", return_value=0) as stop:
            self.assertEqual(runner.main(), 0)
            stop.assert_called_once_with(Path("/board/repo/data/results/hdmi-wall-multi"))

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
