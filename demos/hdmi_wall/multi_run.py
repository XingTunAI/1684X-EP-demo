#!/usr/bin/env python3
"""Run one independent HDMI wall per accelerator and switch pages in one viewer."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

import run as single


LAUNCHER_KIND = "bm1684x-hdmi-wall-multi-v1"


def device_ids(value: str) -> list[int]:
    try:
        items = value.split(",")
        devices = [int(item.strip()) for item in items]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use comma-separated device IDs, for example 0,1") from exc
    if not 1 <= len(devices) <= 4 or any(device < 0 for device in devices):
        raise argparse.ArgumentTypeError("select 1..4 nonnegative device IDs")
    if len(set(devices)) != len(devices):
        raise argparse.ArgumentTypeError("device IDs must be unique")
    return devices


def normalize_device_overrides(value: object) -> dict:
    """Validate JSON per-device options without coercing booleans or fractions."""
    if not isinstance(value, dict):
        raise ValueError("devices must be an object keyed by nonnegative device IDs")
    result = {}
    for key, options in value.items():
        if not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key:
            raise ValueError(f"Invalid device configuration key: {key!r}")
        if not isinstance(options, dict) or set(options) - {"streams", "gate_merge_budget_kib"}:
            raise ValueError(f"Device {key} accepts only streams and gate_merge_budget_kib")
        for name, number in options.items():
            low, high = (1, 32) if name == "streams" else (0, 1024)
            if not isinstance(number, int) or isinstance(number, bool) or not low <= number <= high:
                raise ValueError(f"Device {key}: {name} must be an integer from {low} to {high}")
        result[key] = dict(options)
    return result


def read_device_configuration(path: Path) -> tuple[dict, dict]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or set(document) - {"devices", "context"} or "devices" not in document:
        raise ValueError("Device configuration requires devices and optional context objects")
    context = document.get("context", {})
    if not isinstance(context, dict):
        raise ValueError("Device configuration context must be an object")
    return normalize_device_overrides(document["devices"]), context


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=single.DEFAULT_ROOT,
                        help="Absolute Linux repository directory; required for Windows --dry-run.")
    parser.add_argument("--devices", type=device_ids, default=[0, 1],
                        help="1..4 comma-separated accelerator IDs (default: 0,1).")
    parser.add_argument("--device-config", help="JSON file with devices overrides for streams and gate_merge_budget_kib, plus optional context.")
    parser.add_argument("--streams", "-n", type=single.positive, default=32,
                        help="Independent channels PER DEVICE, from 1 to 32 (default: 32).")
    parser.add_argument("--duration", type=single.positive, default=1800)
    parser.add_argument("--input", default="third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4")
    parser.add_argument("--model", choices=("n", "s"), default="s")
    parser.add_argument("--score-gate", choices=("off", "on"), default="off")
    parser.add_argument("--record-mode", choices=("full", "summary"), default="full",
                        help="Per-frame JSONL (full) or bounded aggregate output (summary).")
    parser.add_argument("--prime-local-decoders", choices=("off", "on"), default="off",
                        help="Experimental local-file first-frame priming before playback clocks start.")
    parser.add_argument("--gate-merge-budget-kib", type=single.gate_merge_budget_kib, default=0,
                        help="Extra score-gate output-read budget per detection, 0..1024 KiB; nonzero requires score-gate on.")
    parser.add_argument("--image-path", choices=("auto", "bgr", "yuv"), default="auto",
                        help="auto selects direct YUV preprocessing when supported; bgr retains the reference conversion path.")
    parser.add_argument("--policy", choices=("latest", "all"), default="latest")
    parser.add_argument("--infer-fps", type=single.nonnegative_finite, default=None,
                        help="Per-channel detection start-rate cap; defaults: latest=5, all=0.")
    parser.add_argument("--max-frame-age-ms", type=single.nonnegative_finite, default=None,
                        help="Discard expired waiting frames; defaults: latest=250, all=0. Use 0 to disable.")
    single.add_observation_arguments(parser)
    parser.add_argument("--fifo-timeout", type=single.positive, default=90,
                        help="Maximum wait for each worker's FIFO, seconds.")
    parser.add_argument("--telemetry-interval", type=single.nonnegative_finite, default=0,
                        help="Per-device bm-smi sampling interval in seconds; 0 disables telemetry (default).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop", action="store_true", help="Stop the latest verified multi-device launcher and all its children.")
    args = parser.parse_args(argv)
    if args.streams > 32:
        parser.error("--streams must be between 1 and 32 per device")
    if args.score_gate == "off" and args.gate_merge_budget_kib != 0:
        parser.error("--gate-merge-budget-kib must be 0 with --score-gate off")
    args.device_overrides = {}
    args.device_configuration_context = {}
    if args.device_config and not args.stop:
        config_path = Path(args.device_config)
        if not config_path.is_absolute():
            if args.root is None:
                parser.error("--root is required to resolve --device-config")
            config_path = Path(args.root) / config_path
        try:
            args.device_overrides, args.device_configuration_context = read_device_configuration(config_path)
            extra = set(args.device_overrides) - {str(device) for device in args.devices}
            if extra:
                raise ValueError("Device configuration contains unselected devices: " + ",".join(sorted(extra)))
            if args.score_gate == "off" and any(options.get("gate_merge_budget_kib", 0) != 0 for options in args.device_overrides.values()):
                raise ValueError("Per-device gate_merge_budget_kib must be 0 with --score-gate off")
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        args.device_config = str(config_path)
    if args.infer_fps is None:
        args.infer_fps = 5.0 if args.policy == "latest" else 0.0
    if args.max_frame_age_ms is None:
        args.max_frame_age_ms = 250.0 if args.policy == "latest" else 0.0
    if args.policy == "all" and args.infer_fps != 0:
        parser.error("--infer-fps must be 0 with --policy all")
    if args.policy == "all" and args.max_frame_age_ms != 0:
        parser.error("--max-frame-age-ms must be 0 with --policy all")
    effective_streams = sum(args.device_overrides.get(str(device), {}).get("streams", args.streams) for device in args.devices)
    if single.live_source(args.input) and effective_streams != 1:
        parser.error("RTSP --input requires one device and --streams 1; use hdmi_wall.pcie --inputs-file for distinct camera sources")
    if args.root is None or not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path (required on non-Linux hosts)")
    if args.stop and args.dry_run:
        parser.error("--stop and --dry-run cannot be combined")
    single.validate_observation(parser, args, [args.device_overrides.get(str(device), {}).get("streams", args.streams)
                                              for device in args.devices])
    return args


def viewer_pcie_labels(context: dict) -> dict[int, str]:
    """Only forward explicitly available link observations, never infer by ID."""
    links = context.get("pcie_devices", [])
    if not isinstance(links, list):
        return {}
    labels, seen = {}, set()
    for link in links:
        if not isinstance(link, dict) or type(link.get("device")) is not int:
            continue
        device = link["device"]
        if device in seen:
            labels.pop(device, None)  # Ambiguous observations are not a label.
            continue
        seen.add(device)
        label = link.get("label")
        if (device >= 0 and link.get("available") is True and isinstance(label, str)
                and len(label) <= 30
                and re.fullmatch(r"PCIe [0-9]+(?:\.[0-9]+)?(?: GT/s)? x[1-9][0-9]*", label)):
            labels[device] = label
    return labels


def build_plan(args: argparse.Namespace) -> dict:
    root = PurePosixPath(args.root)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = root / "data/results/hdmi-wall-multi" / run_id
    python = sys.executable if sys.platform.startswith("linux") else "/usr/bin/python3"
    workers = []
    for device in args.devices:
        worker_values = dict(vars(args), device=device)
        worker_values.update(args.device_overrides.get(str(device), {}))
        worker_args = argparse.Namespace(**worker_values)
        worker = single.build_plan(worker_args)
        device_output = output / f"device_{device}"
        worker["output"] = str(device_output)
        worker["worker_command"][-1] = str(device_output)
        # Reuse single-device file preflight without requiring its ffplay player.
        worker["player_command"] = [python]
        worker.pop("player_environment")
        worker["device"] = device
        worker["fifo"] = str(device_output / "preview.bgr")
        workers.append(worker)
    environment = single.player_environment()
    # The ctypes SDL viewer must load system multimedia libraries, not SOPHON's
    # worker libraries inherited from a shell or build environment.
    environment["LD_LIBRARY_PATH"] = os.environ.get("PLAYER_LIB_PATH") or "/usr/lib/aarch64-linux-gnu"
    pcie_labels = viewer_pcie_labels(args.device_configuration_context)
    manifest = {
        "devices": [{"device": item["device"], "fifo": item["fifo"], "output": item["output"],
                     "streams": item["streams"], "status": "starting",
                     **({"pcie_link_label": pcie_labels[item["device"]]} if item["device"] in pcie_labels else {})}
                    for item in workers],
        "width": 1920, "height": 1080, "fps": 10, "supervised_close": True,
    }
    return {
        "output": str(output), "selected_devices": args.devices,
        "streams_per_device": workers[0]["streams"] if len({item["streams"] for item in workers}) == 1 else None,
        "streams_by_device": {str(item["device"]): item["streams"] for item in workers},
        "total_streams": sum(item["streams"] for item in workers),
        "gate_merge_budget_kib": workers[0]["gate_merge_budget_kib"] if len({item["gate_merge_budget_kib"] for item in workers}) == 1 else None,
        "gate_merge_budget_kib_by_device": {str(item["device"]): item["gate_merge_budget_kib"] for item in workers},
        "record_mode": args.record_mode,
        "prime_local_decoders": args.prime_local_decoders,
        "observe_decode": args.observe_decode, "inference": args.inference,
        "compare_streams": args.compare_streams, "compare_stream_ids": args.compare_stream_ids,
        "observe_preview_fps": args.observe_preview_fps,
        "telemetry_interval_seconds": args.telemetry_interval,
        "device_config": args.device_config, "device_configuration_context": args.device_configuration_context,
        "workers": workers, "viewer_manifest": manifest,
        "viewer_command": [python, str(root / "demos/hdmi_wall/viewer.py"), "--manifest", str(output / "viewer.json")],
        "viewer_environment": environment,
        "shared_lock": str(root / "data/results/hdmi-wall/launcher.lock"),
    }


def required_files(args: argparse.Namespace, plan: dict) -> list[str]:
    names = [plan["viewer_command"][1]]
    for worker in plan["workers"]:
        names.extend(single.required_files(args, worker))
    return list(dict.fromkeys(names))


def viewer_close_requested(output: Path, viewer_pid: int) -> bool:
    """Accept a close request only from the viewer owned by this run.

    The viewer keeps draining every FIFO after requesting close. Its supervisor
    stops workers first, then signals the viewer after their final writes finish.
    Missing, partial, or stale status files must not interrupt a running group.
    """
    try:
        status = json.loads((output / "viewer-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return False
    return (isinstance(status, dict) and status.get("pid") == viewer_pid
            and status.get("close_requested") is True)


class Telemetry:
    """Bounded sampling in the existing supervisor, without a history in memory.

    Stagger cards and perform at most one 2s query per supervisor iteration. A
    stop request is delayed by at most the currently executing query; missed
    samples are never replayed. Timestamps include startup, so consumers must
    filter to each worker's formal measurement window before reporting load.
    """

    def __init__(self, output: Path, root: Path, workers: list[dict], interval: float):
        self.root = root
        self.interval = interval
        self.workers = workers
        # sudo's secure_path commonly omits the SDK bin directory.
        self.bm_smi = shutil.which("bm-smi") or "/opt/sophon/libsophon-current/bin/bm-smi"
        self.path = output / "telemetry.jsonl"
        self.handle = self.path.open("x", encoding="utf-8", buffering=1)
        started = time.monotonic()
        self.due = {item["device"]: started + index * interval / len(workers)
                    for index, item in enumerate(workers)}
        self.stats = {item["device"]: {"samples": 0, "valid_samples": 0, "read_failures": 0,
                                     "sum_percent": 0.0, "min_percent": None, "max_percent": None}
                      for item in workers}
        self.write_error = None
        self.latest_samples = {}

    @staticmethod
    def wall_status(worker: dict) -> dict:
        result = {"worker_status": worker["status"], "configured_streams": worker["streams"]}
        try:
            status = json.loads((Path(worker["output"]) / "status.json").read_text(encoding="utf-8"))
            streams = status["streams"]
            if not isinstance(streams, list) or any(not isinstance(item, dict) for item in streams):
                raise ValueError("Invalid status streams")
            result.update(timestamp_unix_ms=status.get("timestamp_unix_ms"),
                          stream_entries=len(streams),
                          has_image=sum(item.get("has_image") is True for item in streams),
                          stale=sum(item.get("stale") is True for item in streams),
                          failed=status.get("failed"), stopped=status.get("stopped"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(timestamp_unix_ms=None, has_image=None, stale=None, error=str(exc))
        return result

    def tick(self) -> None:
        if self.write_error is not None:
            return
        now = time.monotonic()
        worker = min(self.workers, key=lambda item: self.due[item["device"]])
        device = worker["device"]
        if now < self.due[device]:
            return
        row = {"device": device, "timestamp_unix_s": time.time(), "monotonic_s": now,
               "tpu_util_percent": None, "error": None}
        try:
            query = subprocess.run([self.bm_smi, f"--start_dev={device}", f"--last_dev={device}",
                                    "--text_format", "--noloop"],
                                   capture_output=True, text=True, timeout=2, check=False)
            if query.returncode != 0:
                raise ValueError(f"bm-smi exited {query.returncode}: {query.stderr.strip()[:500]}")
            before_percent, separator, _rest = query.stdout.partition("%")
            # Never strip a minus sign or skip an unavailable first percentage
            # and accidentally report a later, unrelated percentage as TPU.
            match = re.search(r"(?<![\w.+-])([+-]?\d+(?:\.\d+)?)\s*$", before_percent) if separator else None
            if match is None or not 0 <= float(match[1]) <= 100:
                raise ValueError("bm-smi returned no valid TPU percentage")
            row["tpu_util_percent"] = float(match[1])
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            row["error"] = str(exc)[:1000]
        finished = time.monotonic()
        self.due[device] = finished + self.interval
        row["query_duration_ms"] = (finished - now) * 1000
        try:
            row["disk_free_bytes"] = shutil.disk_usage(self.root).free
        except OSError as exc:
            row.update(disk_free_bytes=None, disk_error=str(exc))
        try:
            match = re.search(r"^MemAvailable:\s*(\d+)\s+kB$",
                              Path("/proc/meminfo").read_text(encoding="ascii"), re.MULTILINE)
            if match is None:
                raise ValueError("MemAvailable is missing")
            row["mem_available_bytes"] = int(match[1]) * 1024
        except (OSError, ValueError) as exc:
            row.update(mem_available_bytes=None, memory_error=str(exc))
        row["wall"] = self.wall_status(worker)
        stats = self.stats[device]
        stats["samples"] += 1
        value = row["tpu_util_percent"]
        if value is None:
            stats["read_failures"] += 1
        else:
            stats["valid_samples"] += 1
            stats["sum_percent"] += value
            stats["min_percent"] = value if stats["min_percent"] is None else min(stats["min_percent"], value)
            stats["max_percent"] = value if stats["max_percent"] is None else max(stats["max_percent"], value)
        try:
            self.handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.latest_samples[str(device)] = row
            single.save_json(self.path.with_name("telemetry-live.json"),
                             {"interval_seconds": self.interval, "devices": self.latest_samples})
        except OSError as exc:
            self.write_error = str(exc)
            print(f"HDMI telemetry write failed: {exc}", file=sys.stderr)

    def close(self) -> dict:
        try:
            self.handle.close()
        except OSError as exc:
            self.write_error = str(exc)
        return {"path": str(self.path), "interval_seconds": self.interval, "query_timeout_seconds": 2,
                "scope": "devices contains all supervisor samples, including startup; formal_measurement contains the automatically filtered per-worker formal-window statistics.",
                "write_error": self.write_error,
                "formal_measurement": self.formal_measurement(),
                "devices": {str(device): dict(stats, mean_percent=(stats["sum_percent"] / stats["valid_samples"]
                                                                  if stats["valid_samples"] else None))
                            for device, stats in self.stats.items()}}

    def formal_measurement(self) -> dict:
        """Stream the small telemetry log once; summary mode needs no frame log."""
        devices = {}
        for worker in self.workers:
            record = {"samples": 0, "valid_samples": 0, "read_failures": 0, "sum_percent": 0.0,
                      "min_percent": None, "max_percent": None, "mean_percent": None}
            devices[str(worker["device"])] = record
            try:
                summary = json.loads((Path(worker["output"]) / "summary.json").read_text(encoding="utf-8"))
                start = summary["measurement_start_monotonic_s"]
                end = summary["observed_measurement_end_monotonic_s"]
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                       for value in (start, end)) or end < start:
                    raise ValueError("Invalid formal measurement time bounds")
                record.update(start_monotonic_s=start, end_monotonic_s=end,
                              worker_summary_status=summary.get("status"),
                              accounting_complete=summary.get("accounting_complete"))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                record["error"] = str(exc)
        malformed_rows = 0
        read_error = None
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                        record = devices.get(str(row["device"]))
                        timestamp = row["monotonic_s"]
                        value = row["tpu_util_percent"]
                        if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                                or not math.isfinite(timestamp)):
                            raise ValueError("Invalid sample timestamp")
                        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                                  or not math.isfinite(value) or not 0 <= value <= 100):
                            raise ValueError("Invalid sample value")
                    except (ValueError, KeyError, TypeError):
                        malformed_rows += 1
                        continue
                    if record is None or "error" in record or not record["start_monotonic_s"] <= timestamp < record["end_monotonic_s"]:
                        continue
                    record["samples"] += 1
                    if value is None:
                        record["read_failures"] += 1
                    else:
                        record["valid_samples"] += 1
                        record["sum_percent"] += value
                        record["min_percent"] = value if record["min_percent"] is None else min(record["min_percent"], value)
                        record["max_percent"] = value if record["max_percent"] is None else max(record["max_percent"], value)
        except (OSError, UnicodeError) as exc:
            read_error = str(exc)
        for record in devices.values():
            if record["valid_samples"]:
                record["mean_percent"] = record["sum_percent"] / record["valid_samples"]
        return {"scope": "bm-smi query-start monotonic time in [measurement_start, observed_measurement_end); each worker has its own bounds. Incomplete workers remain explicitly labelled.",
                "devices": devices, "malformed_rows": malformed_rows, "read_error": read_error}


def stop_latest(parent: Path) -> int:
    latest = json.loads((parent / "latest.json").read_text(encoding="utf-8"))
    output = Path(latest["output"]).resolve()
    if output.parent != parent.resolve():
        raise RuntimeError("latest.json points outside this launcher's results directory")

    def read_metadata() -> dict:
        metadata = json.loads((output / "run.json").read_text(encoding="utf-8"))
        if metadata.get("launcher_kind") != LAUNCHER_KIND:
            raise RuntimeError("Unrecognized run.json; refusing to stop processes")
        return metadata

    metadata = read_metadata()
    launcher = metadata.get("launcher")
    sent = single.send_verified(launcher, signal.SIGTERM)
    # Allow the supervisor's bounded worker-first, viewer-last cleanup phases.
    deadline = time.monotonic() + (len(metadata.get("workers", [])) + 1) * (single.STOP_GRACE + single.REAP_TIMEOUT) + 2
    while sent and single.is_same_process(launcher) and time.monotonic() < deadline:
        time.sleep(0.1)
    metadata = read_metadata()  # A child may have started before TERM was handled.
    identities = [item.get("identity") for item in metadata.get("workers", [])] + [metadata.get("viewer")]
    for identity in identities:
        sent = single.send_verified(identity, signal.SIGTERM, group=True) or sent
        deadline = time.monotonic() + single.STOP_GRACE
        while single.is_same_process(identity) and time.monotonic() < deadline:
            time.sleep(0.1)
        single.send_verified(identity, signal.SIGKILL, group=True)
        deadline = time.monotonic() + single.REAP_TIMEOUT
        while single.is_same_process(identity) and time.monotonic() < deadline:
            time.sleep(0.1)
    if sent and single.is_same_process(launcher):
        single.send_verified(launcher, signal.SIGKILL)
    if any(single.is_same_process(identity) for identity in [launcher, *identities]):
        print("Some verified processes remain alive", file=sys.stderr)
        return 1
    print(f"Stopped verified multi-device HDMI processes: {output}" if sent else f"No matching live processes: {output}")
    return 0


def run(args: argparse.Namespace, plan: dict) -> int:
    import fcntl  # Linux only; planning and unit tests can run on Windows.

    parent = Path(plan["output"]).parent
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(plan["shared_lock"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("An HDMI wall launcher already holds the shared lock; stop that launcher first") from exc
        loads = single.other_loads()
        if loads:
            raise RuntimeError("Existing accelerator load detected; refusing to start: " + json.dumps(loads))
        for name in required_files(args, plan):
            if not Path(name).is_file():
                raise RuntimeError(f"Required file is missing: {name}")
        for name in {plan["viewer_command"][0], *(item["worker_command"][0] for item in plan["workers"])}:
            if not os.access(name, os.X_OK):
                raise RuntimeError(f"File is not executable: {name}")
        output = Path(plan["output"])
        output.mkdir(mode=0o755, exist_ok=False)
        metadata = dict(plan, launcher_kind=LAUNCHER_KIND, launcher=single.process_identity(os.getpid()),
                        device_snapshot=single.device_snapshot(), started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        status="starting")
        # Independent mutable state for each device, persisted for verified --stop.
        metadata["workers"] = [dict(item, identity=None, status="starting", returncode=None) for item in plan["workers"]]
        manifest = dict(plan["viewer_manifest"], devices=[dict(item) for item in plan["viewer_manifest"]["devices"]])
        workers: list[tuple[subprocess.Popen, dict | None]] = []
        viewer: tuple[subprocess.Popen, dict | None] | None = None
        logs = []
        signals = []
        previous_handlers = {}
        startup_deadlines: dict[int, float] = {}
        ready = set()
        result = 1
        telemetry = None

        def requested_stop(signum: int, _frame: object) -> None:
            signals.append(signum)

        def publish() -> None:
            for record, page in zip(metadata["workers"], manifest["devices"]):
                pending = output / f"device_{record['device']}.worker.log"
                device_output = Path(record["output"])
                if device_output.is_dir() and pending.exists():
                    os.replace(pending, device_output / "worker.log")
                page["status"] = record["status"]
                page["returncode"] = record["returncode"]
            single.save_json(output / "viewer.json", manifest)
            single.save_json(output / "run.json", metadata)

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, requested_stop)
        try:
            if args.telemetry_interval:
                telemetry = Telemetry(output, Path(args.root), metadata["workers"], args.telemetry_interval)
            publish()
            single.save_json(parent / "latest.json", {"output": str(output), "run_id": output.name})
            viewer_log = (output / "viewer.log").open("xb", buffering=0)
            logs.append(viewer_log)
            print("Viewer: " + shlex.join(plan["viewer_command"]), flush=True)
            process = subprocess.Popen(plan["viewer_command"], cwd=args.root,
                                       env=dict(os.environ, **plan["viewer_environment"]),
                                       stdout=viewer_log, stderr=subprocess.STDOUT, start_new_session=True)
            viewer = (process, None)  # Register ownership before exec identity capture.
            metadata["viewer"] = single.capture_child_identity(process, plan["viewer_command"])
            viewer = (process, metadata["viewer"])
            publish()
            # The viewer opens every FIFO as it appears and drains hidden pages;
            # waiting for all workers first would exhaust early workers' 20s wait.
            for record in metadata["workers"]:
                if signals or viewer[0].poll() is not None:
                    break
                log = (output / f"device_{record['device']}.worker.log").open("xb", buffering=0)
                logs.append(log)
                print(f"Device {record['device']}: " + shlex.join(record["worker_command"]), flush=True)
                worker = subprocess.Popen(record["worker_command"], cwd=args.root, stdout=log,
                                          stderr=subprocess.STDOUT, start_new_session=True)
                workers.append((worker, None))
                record["identity"] = single.capture_child_identity(worker, record["worker_command"])
                workers[-1] = (worker, record["identity"])
                startup_deadlines[record["device"]] = time.monotonic() + args.fifo_timeout
                publish()
            print(f"Multi-device HDMI wall launched; logs: {output}", flush=True)
            failed = False
            while True:
                if signals:
                    result = 128 + signals[0]
                    metadata["exit_reason"] = f"launcher received signal {signals[0]}"
                    break
                changed = False
                for record, child in zip(metadata["workers"], workers):
                    worker, identity = child
                    code = worker.poll()
                    if code is not None:
                        if record["returncode"] is None:
                            record["returncode"] = code
                            record["status"] = "completed" if code == 0 else "failed"
                            if code != 0:
                                failed = True
                            changed = True
                        continue
                    if record["device"] not in ready:
                        try:
                            fifo_ready = stat.S_ISFIFO(Path(record["fifo"]).stat().st_mode)
                        except FileNotFoundError:
                            fifo_ready = False
                        if fifo_ready:
                            ready.add(record["device"])
                            record["status"] = "running"
                            changed = True
                        elif time.monotonic() >= startup_deadlines[record["device"]]:
                            record["error"] = f"Timed out waiting for FIFO: {record['fifo']}"
                            single.stop_children([child])
                            record["returncode"] = worker.poll()
                            record["status"] = "failed"
                            failed = changed = True
                if changed:
                    metadata["status"] = "running_with_errors" if failed else "running"
                    publish()
                viewer_code = viewer[0].poll()
                if viewer_code is None and viewer_close_requested(output, viewer[0].pid):
                    result = 1 if failed else 0
                    metadata["exit_reason"] = "viewer requested close"
                    break
                if len(workers) == len(metadata["workers"]) and all(record["returncode"] is not None for record in metadata["workers"]):
                    result = single.exit_status(viewer_code) if viewer_code else (1 if failed else 0)
                    metadata["exit_reason"] = "all workers exited"
                    break
                if viewer_code is not None:
                    result = single.exit_status(viewer_code) if viewer_code else (1 if failed else 0)
                    metadata["exit_reason"] = "viewer exited; stopping all workers"
                    if len(workers) < len(metadata["workers"]):
                        result = result or 1
                    break
                if telemetry is not None:
                    telemetry.tick()
                time.sleep(0.1)
        except Exception as exc:
            metadata["error"] = str(exc)
            print(f"Multi-device HDMI wall error: {exc}", file=sys.stderr)
            result = 1
        finally:
            # Never stop the viewer while a worker is still draining its FIFO.
            single.stop_children(workers + ([viewer] if viewer is not None else []))
            for handle in logs:
                handle.close()
            for record, (worker, _identity) in zip(metadata["workers"], workers):
                record["returncode"] = worker.poll()
                if record["status"] not in ("completed", "failed"):
                    record["status"] = "completed" if record["returncode"] == 0 else "stopped"
            for record in metadata["workers"][len(workers):]:
                record["status"] = "not_started"
            metadata.update(status="stopped", exit_status=result,
                            viewer_returncode=viewer[0].poll() if viewer else None,
                            finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
            try:
                if telemetry is not None:
                    try:
                        metadata["telemetry"] = telemetry.close()
                        if metadata["telemetry"].get("write_error"):
                            result = result or 1
                        single.save_json(output / "telemetry-summary.json", metadata["telemetry"])
                    except (OSError, ValueError, RuntimeError) as exc:
                        # An optional attachment must not prevent the essential
                        # final worker identities/status from being published.
                        metadata["telemetry_error"] = str(exc)
                        result = result or 1
                    metadata["exit_status"] = result
                publish()
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)
        return result


def main() -> int:
    args = arguments()
    if args.dry_run:
        print(json.dumps(build_plan(args), ensure_ascii=False, indent=2))
        return 0
    if not sys.platform.startswith("linux"):
        print("Launching and stopping require the Linux board; use --dry-run here.", file=sys.stderr)
        return 2
    try:
        if args.stop:
            return stop_latest(Path(args.root) / "data/results/hdmi-wall-multi")
        return run(args, build_plan(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Multi-device HDMI wall: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
