import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "showcase.py"
with patch.object(sys, "path", [str(RUNNER_PATH.parent), *sys.path]):
    spec = importlib.util.spec_from_file_location("_hdmi_showcase_runner", RUNNER_PATH)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)


class ShowcaseTests(unittest.TestCase):
    def parse(self, *extra):
        return runner.arguments(["--root", "/userdata/board repo", "--devices", "0,1", *extra])

    def off_board_plan(self, args):
        with patch.object(runner.sys, "platform", "win32"):
            return runner.build_plan(args)

    def test_four_hour_default_rounding_summary_and_concurrent_pages(self):
        args = self.parse()
        plan = self.off_board_plan(args)
        self.assertEqual(plan["duration_seconds"], 14400)
        self.assertEqual(plan["material_seconds"], 15000)
        self.assertEqual(plan["telemetry_interval_seconds"], 5)
        self.assertEqual(plan["record_mode"], "summary")
        self.assertEqual(plan["prime_local_decoders"], "on")
        self.assertEqual(plan["run_command"][-len(runner.FIXED_OPTIONS):], runner.FIXED_OPTIONS)
        self.assertEqual(plan["device_configuration"]["devices"],
                         {"0": runner.DEFAULT_CARD_PROFILE, "1": runner.DEFAULT_CARD_PROFILE})
        self.assertTrue(all(not link["available"] and link["generation"] is None
                            for link in plan["device_configuration"]["context"]["pcie_devices"]))
        for duration, seconds in ((537, 600), (538, 1200), (14337, 14400), (14338, 15000)):
            with self.subTest(duration=duration):
                self.assertEqual(self.off_board_plan(self.parse("--duration", str(duration)))["material_seconds"], seconds)

    def test_real_pci_endpoint_is_used_instead_of_root_port_or_device_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = root / "0004:40:00.0"
            endpoint = port / "0004:41:00.0"
            leaf = endpoint / "bm-sophon/bm-sophon0"
            # Windows filenames cannot contain BDF colons; model sysfs reads
            # while exercising the real nearest-ancestor selection and parser.
            contents = {port / "current_link_speed": "5.0 GT/s PCIe", port / "current_link_width": "1",
                        endpoint / "current_link_speed": "8.0 GT/s PCIe", endpoint / "current_link_width": "2"}
            with patch.object(Path, "resolve", return_value=leaf), \
                    patch.object(Path, "read_text", autospec=True, side_effect=lambda path, **_kw: contents[path]):
                link = runner.device_link(0, root / "class")
            self.assertEqual(link["pci_address"], "0004:41:00.0")
            self.assertEqual(link["label"], "PCIe 3.0 x2")
            self.assertEqual(link["generation"], 3)
            contents[endpoint / "current_link_speed"] = "Unknown speed"
            with patch.object(Path, "resolve", return_value=leaf), \
                    patch.object(Path, "read_text", autospec=True, side_effect=lambda path, **_kw: contents[path]), \
                    self.assertRaises(ValueError):
                runner.device_link(0, root / "class")

    def test_auto_discovery_accepts_four_cards_but_requires_selection_for_more(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bm-sophon3", "bm-sophon1", "bm-sophon0", "bm-sophon2", "unrelated", "bm-sophon00"):
                (root / name).mkdir()
            self.assertEqual(runner.discover_devices(root), [0, 1, 2, 3])
            (root / "bm-sophon4").mkdir()
            with self.assertRaises(ValueError):
                runner.discover_devices(root)
        args = self.parse("--devices", "auto")
        links = [{"device": device, "available": True, "generation": 2 if device % 2 else 3}
                 for device in range(4)]
        with patch.object(runner.sys, "platform", "linux"), \
                patch.object(runner, "discover_devices", return_value=[0, 1, 2, 3]), \
                patch.object(runner, "device_link", side_effect=links) as read_link:
            plan = runner.build_plan(args)
        self.assertEqual(plan["devices"], [0, 1, 2, 3])
        self.assertEqual(read_link.call_count, 4)
        self.assertEqual(plan["device_configuration"]["context"]["pcie_devices"], links)

    def test_profile_heterogeneous_options_reach_multi_plan_with_link_context(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile with spaces.json"
            profile.write_text(json.dumps({"devices": {"0": {"streams": 20, "gate_merge_budget_kib": 64},
                                                        "1": {"streams": 32, "gate_merge_budget_kib": 128},
                                                        "3": {"streams": 10}}, "context": {"purpose": "trial"}}), encoding="utf-8")
            plan = self.off_board_plan(self.parse("--profile", str(profile)))
            self.assertEqual(set(plan["device_configuration"]["devices"]), {"0", "1"})
            configuration = Path(directory) / "generated config.json"
            configuration.write_text(json.dumps(plan["device_configuration"]), encoding="utf-8")
            options = plan["run_command"][2:]
            options[options.index("--device-config") + 1] = str(configuration)
            multi_args = runner.multi_run.arguments(options)
            with patch.object(runner.multi_run.single, "player_environment", return_value={}):
                multi_plan = runner.multi_run.build_plan(multi_args)
            self.assertEqual(multi_plan["streams_by_device"], {"0": 20, "1": 32})
            self.assertEqual(multi_plan["gate_merge_budget_kib_by_device"], {"0": 64, "1": 128})
            self.assertEqual(multi_plan["record_mode"], "summary")
            self.assertEqual(multi_plan["prime_local_decoders"], "on")
            self.assertEqual(multi_plan["telemetry_interval_seconds"], 5)
            self.assertEqual(multi_plan["device_configuration_context"]["profile_context"], {"purpose": "trial"})

    def test_link_profiles_follow_measured_link_and_explicit_overrides_take_precedence(self):
        links = [{"device": 0, "available": True, "generation": 3, "current_link_width": 2},
                 {"device": 1, "available": True, "generation": 2, "current_link_width": 1}]
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text('{"devices":{"1":{"streams":12}}}', encoding="utf-8")
            args = self.parse("--profile", str(profile))
            with patch.object(runner.sys, "platform", "linux"), \
                    patch.object(runner, "device_link", side_effect=links), \
                    patch.dict(runner.DEFAULT_LINK_PROFILES, {(2, 1): {"streams": 20, "gate_merge_budget_kib": 64},
                                                             (3, 2): {"streams": 32, "gate_merge_budget_kib": 128}}, clear=True):
                plan = runner.build_plan(args)
        self.assertEqual(plan["device_configuration"]["devices"],
                         {"0": {"streams": 32, "gate_merge_budget_kib": 128},
                          "1": {"streams": 12, "gate_merge_budget_kib": 64}})
        self.assertEqual(plan["device_configuration"]["context"]["profile_selection"]["1"]["explicit_overrides"], {"streams": 12})

    def test_stress_retains_all_cards_and_the_same_preview_launcher(self):
        plan = self.off_board_plan(self.parse("--mode", "stress", "--devices", "3,1,0,2"))
        self.assertEqual(plan["devices"], [3, 1, 0, 2])
        self.assertEqual(plan["mode"], "stress")
        self.assertEqual(plan["run_command"][1], "/userdata/board repo/demos/hdmi_wall/multi_run.py")
        self.assertEqual(set(plan["device_configuration"]["devices"]), {"0", "1", "2", "3"})

    def test_modes_choose_distinct_actual_link_profiles_and_mark_unknown_fallback(self):
        # Deliberately reverse the board's usual device IDs to prohibit ID-based
        # assumptions. Two other cards exercise an unmeasured and unavailable link.
        links = [{"device": 0, "available": True, "generation": 3, "current_link_width": 2},
                 {"device": 1, "available": True, "generation": 2, "current_link_width": 1},
                 {"device": 2, "available": True, "generation": 3, "current_link_width": 1},
                 {"device": 3, "available": False, "generation": 2, "current_link_width": 1}]
        for mode, gen2_streams, gen2_budget in (("showcase", 32, 128), ("stress", 20, 64)):
            with self.subTest(mode=mode), patch.object(runner.sys, "platform", "linux"), \
                    patch.object(runner, "device_link", side_effect=links):
                plan = runner.build_plan(self.parse("--devices", "0,1,2,3", "--mode", mode))
            document = plan["device_configuration"]
            self.assertEqual(document["devices"], {
                "0": {"streams": 32, "gate_merge_budget_kib": 64},
                "1": {"streams": gen2_streams, "gate_merge_budget_kib": gen2_budget},
                "2": {"streams": 32, "gate_merge_budget_kib": 64},
                "3": {"streams": 32, "gate_merge_budget_kib": 64}})
            for device in (2, 3):
                self.assertEqual(document["context"]["profile_selection"][str(device)]["default_source"], "unvalidated_fallback")
            self.assertEqual(plan["prime_local_decoders"], "on")
            self.assertEqual(plan["record_mode"], "summary")
            self.assertEqual(plan["duration_seconds"], 14400)
            self.assertEqual(document["context"]["mode_goal"], runner.MODE_GOALS[mode])
            self.assertIn("No TPU utilization guarantee", document["context"]["profile_validation_scope"])

    def test_explicit_profile_overrides_stress_without_changing_prime_or_fixed_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text('{"devices":{"0":{"streams":24},"1":{"gate_merge_budget_kib":128}}}', encoding="utf-8")
            links = [{"device": 0, "available": True, "generation": 2, "current_link_width": 1},
                     {"device": 1, "available": True, "generation": 3, "current_link_width": 2}]
            with patch.object(runner.sys, "platform", "linux"), patch.object(runner, "device_link", side_effect=links):
                plan = runner.build_plan(self.parse("--mode", "stress", "--profile", str(profile)))
        self.assertEqual(plan["device_configuration"]["devices"], {
            "0": {"streams": 24, "gate_merge_budget_kib": 64}, "1": {"streams": 32, "gate_merge_budget_kib": 128}})
        self.assertEqual(plan["run_command"][-len(runner.FIXED_OPTIONS):], runner.FIXED_OPTIONS)
        self.assertEqual(plan["prime_local_decoders"], "on")

    def test_stop_never_probes_sysfs_reads_profile_or_prepares_video(self):
        args = self.parse("--devices", "auto", "--stop", "--profile", "/missing/profile.json")
        with patch.object(runner, "discover_devices", side_effect=AssertionError("sysfs")), \
                patch.object(runner, "device_link", side_effect=AssertionError("sysfs")), \
                patch.object(runner, "check_existing_run", side_effect=AssertionError("busy check")), \
                patch.object(runner.multi_run, "read_device_configuration", side_effect=AssertionError("profile")), \
                patch.object(runner.subprocess, "run") as prepare, patch.object(runner.os, "execv") as execute:
            plan = runner.build_plan(args)
            self.assertEqual(runner.execute(args, plan), 0)
            prepare.assert_not_called()
            self.assertEqual(execute.call_args.args[1][2:], ["--root", args.root, "--stop"])

    def test_prepare_failure_creates_no_configuration_and_cannot_launch(self):
        args = self.parse()
        plan = self.off_board_plan(args)
        with tempfile.TemporaryDirectory() as directory:
            plan["device_config_path"] = str(Path(directory) / "run/device-config.json")
            with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)), \
                    patch.object(runner.os, "execv") as execute:
                self.assertEqual(runner.execute(args, plan), 7)
                execute.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_execute_publishes_exact_config_then_executes_safe_argv_preserving_environment(self):
        root = "/userdata/board repo; $(touch never) 'quoted'"
        args = self.parse("--root", root)
        plan = self.off_board_plan(args)
        self.assertEqual(plan["run_command"][1], root + "/demos/hdmi_wall/multi_run.py")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run/device-config.json"
            plan["device_config_path"] = str(path)
            plan["run_command"][plan["run_command"].index("--device-config") + 1] = str(path)
            def execute(_python, command):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), plan["device_configuration"])
                self.assertEqual(command, plan["run_command"])
            with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as prepare, \
                    patch.object(runner.os, "execv", side_effect=execute) as launched:
                self.assertEqual(runner.execute(args, plan), 0)
                prepare.assert_called_once_with(plan["prepare_command"], cwd=root, check=False)
                launched.assert_called_once()

    def test_dry_run_on_windows_does_not_write_or_start_processes_or_guess_links(self):
        with patch.object(runner.sys, "platform", "win32"), \
                patch.object(runner, "device_link", side_effect=AssertionError("sysfs")), \
                patch.object(runner, "check_existing_run", side_effect=AssertionError("busy check")), \
                patch.object(runner.subprocess, "run", side_effect=AssertionError("subprocess")), \
                patch.object(runner.os, "execv", side_effect=AssertionError("exec")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                patch("builtins.open", side_effect=AssertionError("file access")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main(["--root", "/missing/repo", "--devices", "0,1,2,3", "--dry-run"]), 0)
        self.assertEqual(len(json.loads(output.getvalue())["device_configuration"]["devices"]), 4)
        with patch.object(runner.sys, "platform", "win32"), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(runner.main(["--root", "/missing/repo", "--dry-run"]), 1)
            self.assertIn("real sysfs", error.getvalue())

    def test_invalid_parameters_and_profile_are_rejected(self):
        for options in (("--duration", "0"), ("--devices", "0,0"), ("--devices", "0,1,2,3,4"),
                        ("--mode", "only-active-page"), ("--root", "relative"),
                        ("--telemetry-interval", "nan"), ("--stop", "--dry-run")):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parse(*options)
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "bad.json"
            profile.write_text('{"devices":{"0":{"streams":33}}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.off_board_plan(self.parse("--profile", str(profile)))


class EarlyBusyTests(unittest.TestCase):
    def metadata(self, root, directory="hdmi-wall-multi", *, launcher=None, worker=None, kind=None):
        parent = root / "data/results" / directory
        output = parent / "existing-run"
        output.mkdir(parents=True)
        (parent / "latest.json").write_text(json.dumps({"output": str(output)}), encoding="utf-8")
        kind = kind or (runner.multi_run.LAUNCHER_KIND if directory == "hdmi-wall-multi" else "bm1684x-hdmi-wall-v1")
        value = {"launcher_kind": kind, "launcher": launcher}
        if directory == "hdmi-wall-multi":
            value["workers"] = [{"identity": worker}]
        else:
            value["worker"] = worker
        (output / "run.json").write_text(json.dumps(value), encoding="utf-8")
        return output

    def test_live_multi_launcher_or_owned_worker_blocks_before_prepare_and_config_writes(self):
        for live_pid in (71, 72):
            with self.subTest(live_pid=live_pid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = self.metadata(root, launcher={"pid": 71}, worker={"pid": 72})
                args = types.SimpleNamespace(root=str(root))
                plan = {"stop": False, "prepare_command": ["prepare"],
                        "device_config_path": str(root / "new-run/device-config.json")}
                with patch.object(runner.multi_run.single, "is_same_process", side_effect=lambda item: item is not None and item["pid"] == live_pid), \
                        patch.object(runner.subprocess, "run") as prepare, patch.object(runner.os, "execv") as execute:
                    with self.assertRaises(RuntimeError) as raised:
                        runner.execute(args, plan)
                prepare.assert_not_called()
                execute.assert_not_called()
                self.assertFalse((root / "new-run").exists())
                self.assertIn(str(output), str(raised.exception))
                self.assertIn("showcase.sh", str(raised.exception))
                self.assertIn("--stop", str(raised.exception))
                self.assertIn(f"verified PID {live_pid}", str(raised.exception))

    def test_live_single_run_reports_its_correct_stop_entry_and_quotes_paths(self):
        with tempfile.TemporaryDirectory(prefix="hdmi space ") as directory:
            root = Path(directory)
            output = self.metadata(root, "hdmi-wall", launcher={"pid": 81})
            with patch.object(runner.multi_run.single, "is_same_process", side_effect=lambda item: item == {"pid": 81}):
                with self.assertRaises(RuntimeError) as raised:
                    runner.check_existing_run(root)
            self.assertIn(str(output), str(raised.exception))
            command = str(raised.exception).splitlines()[-1].strip()
            self.assertEqual(runner.shlex.split(command), ["sudo", "bash", str(root / "demos/hdmi_wall/run.sh"),
                                                          "--root", str(root), "--stop"])

    def test_stale_or_unrecognized_metadata_does_not_block_or_write_files(self):
        for kind in (runner.multi_run.LAUNCHER_KIND, "unrecognized-launcher"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.metadata(root, launcher={"pid": 71}, kind=kind)
                existing = {path.relative_to(root) for path in root.rglob("*")}
                with patch.object(runner.multi_run.single, "is_same_process", return_value=False), \
                        patch.object(runner, "shared_lock_owner", return_value=None) as lock:
                    runner.check_existing_run(root)
                    lock.assert_called_once_with(root / "data/results/hdmi-wall/launcher.lock")
                self.assertEqual({path.relative_to(root) for path in root.rglob("*")}, existing)

    def test_shared_flock_is_inspected_without_creating_or_acquiring_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "launcher.lock"
            locks = root / "proc-locks"
            lock.write_text("unchanged", encoding="ascii")
            inode = lock.stat().st_ino
            locks.write_text(f"10: -> FLOCK ADVISORY WRITE 70 08:01:{inode} 0 EOF\n"
                             f"11: POSIX ADVISORY WRITE 71 08:01:{inode} 0 EOF\n"
                             f"12: FLOCK ADVISORY WRITE 72 08:02:{inode} 0 EOF\n"
                             f"13: FLOCK ADVISORY WRITE 73 08:01:{inode} 0 EOF\n", encoding="ascii")
            original_open = Path.open
            def read_only(path, mode="r", *args, **kwargs):
                self.assertEqual(mode, "r")
                return original_open(path, mode, *args, **kwargs)
            with patch.object(runner.os, "major", return_value=8, create=True), \
                    patch.object(runner.os, "minor", return_value=1, create=True), \
                    patch.object(Path, "open", new=read_only):
                self.assertEqual(runner.shared_lock_owner(lock, locks), 73)
                self.assertIsNone(runner.shared_lock_owner(root / "missing.lock", locks))
            self.assertEqual(lock.read_text(encoding="ascii"), "unchanged")
            self.assertFalse((root / "missing.lock").exists())

    def test_held_lock_blocks_the_prepublication_window_without_claiming_a_run_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(runner, "shared_lock_owner", return_value=91):
                with self.assertRaises(RuntimeError) as raised:
                    runner.check_existing_run(root)
            self.assertIn("held by PID 91", str(raised.exception))
            self.assertIn("directory is not yet available", str(raised.exception))
            self.assertIn("--stop", str(raised.exception))
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
