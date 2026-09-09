#!/usr/bin/env python3
"""Run one independent HDMI wall per accelerator and switch pages in one viewer."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath
import shlex
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


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=single.DEFAULT_ROOT,
                        help="Absolute Linux repository directory; required for Windows --dry-run.")
    parser.add_argument("--devices", type=device_ids, default=[0, 1],
                        help="1..4 comma-separated accelerator IDs (default: 0,1).")
    parser.add_argument("--streams", "-n", type=single.positive, default=32,
                        help="Independent channels PER DEVICE, from 1 to 32 (default: 32).")
    parser.add_argument("--duration", type=single.positive, default=1800)
    parser.add_argument("--input", default="third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4")
    parser.add_argument("--model", choices=("n", "s"), default="s")
    parser.add_argument("--score-gate", choices=("off", "on"), default="off")
    parser.add_argument("--policy", choices=("latest", "all"), default="latest")
    parser.add_argument("--infer-fps", type=single.nonnegative_finite, default=None,
                        help="Per-channel detection start-rate cap; defaults: latest=5, all=0.")
    parser.add_argument("--max-frame-age-ms", type=single.nonnegative_finite, default=None,
                        help="Discard expired waiting frames; defaults: latest=250, all=0. Use 0 to disable.")
    parser.add_argument("--fifo-timeout", type=single.positive, default=90,
                        help="Maximum wait for each worker's FIFO, seconds.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop", action="store_true", help="Stop the latest verified multi-device launcher and all its children.")
    args = parser.parse_args(argv)
    if args.streams > 32:
        parser.error("--streams must be between 1 and 32 per device")
    if args.infer_fps is None:
        args.infer_fps = 5.0 if args.policy == "latest" else 0.0
    if args.max_frame_age_ms is None:
        args.max_frame_age_ms = 250.0 if args.policy == "latest" else 0.0
    if args.policy == "all" and args.infer_fps != 0:
        parser.error("--infer-fps must be 0 with --policy all")
    if args.policy == "all" and args.max_frame_age_ms != 0:
        parser.error("--max-frame-age-ms must be 0 with --policy all")
    if single.live_source(args.input) and (args.streams != 1 or len(args.devices) != 1):
        parser.error("RTSP --input requires one device and --streams 1; use hdmi_wall.pcie --inputs-file for distinct camera sources")
    if args.root is None or not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path (required on non-Linux hosts)")
    if args.stop and args.dry_run:
        parser.error("--stop and --dry-run cannot be combined")
    return args


def build_plan(args: argparse.Namespace) -> dict:
    root = PurePosixPath(args.root)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = root / "data/results/hdmi-wall-multi" / run_id
    python = sys.executable if sys.platform.startswith("linux") else "/usr/bin/python3"
    workers = []
    for device in args.devices:
        worker_args = argparse.Namespace(**vars(args), device=device)
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
    manifest = {
        "devices": [{"device": item["device"], "fifo": item["fifo"], "output": item["output"],
                     "streams": args.streams, "status": "starting"} for item in workers],
        "width": 1920, "height": 1080, "fps": 10, "supervised_close": True,
    }
    return {
        "output": str(output), "selected_devices": args.devices,
        "streams_per_device": args.streams, "total_streams": args.streams * len(args.devices),
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
                publish()
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)
        return result


def main() -> int:
    args = arguments()
    plan = build_plan(args)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not sys.platform.startswith("linux"):
        print("Launching and stopping require the Linux board; use --dry-run here.", file=sys.stderr)
        return 2
    try:
        if args.stop:
            return stop_latest(Path(args.root) / "data/results/hdmi-wall-multi")
        return run(args, plan)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Multi-device HDMI wall: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
