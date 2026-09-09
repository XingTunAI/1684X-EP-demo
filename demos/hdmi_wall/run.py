#!/usr/bin/env python3
"""Run the BM1684X HDMI wall and its local player as a supervised pair."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path, PurePosixPath
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid


DEFAULT_ROOT = str(Path(__file__).resolve().parents[2]) if sys.platform.startswith("linux") else None
LOAD_NAMES = {"stream_threads.pcie", "single_card_pipeline.pcie", "hdmi_wall.pcie"}
STOP_GRACE = 5.0
REAP_TIMEOUT = 2.0
IDENTITY_TIMEOUT = 1.0


def player_environment() -> dict[str, str]:
    result = {
        "DISPLAY": os.environ.get("DISPLAY") or os.environ.get("DISPLAY_ID") or ":0",
        "LD_LIBRARY_PATH": os.environ.get("PLAYER_LIB_PATH") or os.environ.get("LD_LIBRARY_PATH") or "/usr/lib/aarch64-linux-gnu",
        "SDL_RENDER_DRIVER": os.environ.get("SDL_RENDER_DRIVER") or "software",
    }
    authority = os.environ.get("XAUTHORITY")
    if not authority and sys.platform.startswith("linux"):
        # Prefer the current user's standard X11 authority. LightDM is an
        # optional discovered fallback, not an assumed display-manager setup.
        for candidate in (Path.home() / ".Xauthority", Path("/var/run/lightdm/root/:0")):
            if candidate.is_file():
                authority = str(candidate)
                break
    if authority:
        result["XAUTHORITY"] = authority
    # On non-Linux preview hosts, do not present the host's home directory as
    # the remote board's X11 authority. If unspecified, leave it to the board.
    return result


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative_finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return number


def gate_merge_budget_kib(value: str) -> int:
    number = int(value)
    if number < 0 or number > 1024:
        raise argparse.ArgumentTypeError("must be an integer between 0 and 1024 KiB")
    return number


def live_source(source: str) -> bool:
    return source.startswith(("rtsp://", "rtsps://"))


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Absolute board repository directory; inferred on Linux, required for Windows --dry-run.")
    parser.add_argument("--duration", type=positive, default=1800, help="Run duration in seconds.")
    parser.add_argument(
        "--streams", "-n", type=positive, default=1, metavar="N",
        help="Number of independent channels: any integer 1..32 (default: 1). Common values: 1, 2, 4, 8, 16, 24, 32.",
    )
    parser.add_argument(
        "--list-streams", action="version",
        version="Supported channels: any integer from 1 to 32. Default: 1.\nCommon values: 1, 2, 4, 8, 16, 24, 32.\nExample: sudo bash demos/hdmi_wall/run.sh --streams 8",
        help="Show supported channel counts and exit without starting the demo.",
    )
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--input", default="third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4")
    parser.add_argument("--model", choices=("n", "s"), default="s")
    parser.add_argument("--policy", choices=("all", "latest"), default="latest",
                        help="latest keeps only the newest waiting frame; all processes frames sequentially (default: latest).")
    parser.add_argument("--infer-fps", type=nonnegative_finite, default=0.0,
                        help="Per-channel detection start-rate limit; 0 runs as capacity allows. Requires latest when nonzero.")
    parser.add_argument("--max-frame-age-ms", type=nonnegative_finite, default=None,
                        help="Discard stale frames before detection: age since local frame due time or RTSP decode completion. 0 disables; defaults: latest=250, all=0.")
    parser.add_argument("--score-gate", choices=("off", "on"), default="off")
    parser.add_argument("--record-mode", choices=("full", "summary"), default="full",
                        help="full writes per-frame JSONL; summary retains bounded aggregate measurement output.")
    parser.add_argument("--prime-local-decoders", choices=("off", "on"), default="off",
                        help="Experimental: wait for each local file's first decoded frame before starting its playback clock.")
    parser.add_argument("--gate-merge-budget-kib", type=gate_merge_budget_kib, default=0,
                        help="Extra output-read budget for merging small score-gate ranges, 0..1024 KiB; nonzero requires score-gate on.")
    parser.add_argument("--image-path", choices=("auto", "bgr", "yuv"), default="auto",
                        help="auto uses direct YUV preprocessing when supported; bgr retains the reference conversion path.")
    parser.add_argument("--fifo-timeout", type=positive, default=90, help="Maximum FIFO startup wait, seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without checking board files or launching anything.")
    parser.add_argument("--stop", action="store_true", help="Stop the latest verified launcher and its children.")
    args = parser.parse_args(argv)
    if args.device < 0:
        parser.error("--device must be nonnegative")
    if args.streams > 32:
        parser.error("--streams must be between 1 and 32")
    if args.score_gate == "off" and args.gate_merge_budget_kib != 0:
        parser.error("--gate-merge-budget-kib must be 0 with --score-gate off")
    if args.max_frame_age_ms is None:
        args.max_frame_age_ms = 250.0 if args.policy == "latest" else 0.0
    if args.policy == "all" and args.infer_fps != 0:
        parser.error("--infer-fps must be 0 with --policy all")
    if args.policy == "all" and args.max_frame_age_ms != 0:
        parser.error("--max-frame-age-ms must be 0 with --policy all")
    if live_source(args.input) and args.streams > 1:
        parser.error("RTSP --input requires --streams 1; use hdmi_wall.pcie --inputs-file for distinct camera sources")
    if args.root is None:
        parser.error("--root is required on non-Linux hosts; supply the absolute repository path on the board")
    if not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path")
    if args.stop and args.dry_run:
        parser.error("--stop and --dry-run cannot be combined")
    return args


def build_plan(args: argparse.Namespace) -> dict:
    root = PurePosixPath(args.root)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = root / "data/results/hdmi-wall" / run_id
    demo = root / "third_party/sophon-demo/sample/YOLOv8_plus_det"
    if live_source(args.input):
        source = args.input
    else:
        source_path = PurePosixPath(args.input)
        source = str(source_path if source_path.is_absolute() else root / source_path)
    model = demo / f"models/BM1684X/yolov8{args.model}_int8_1b.bmodel"
    classes = demo / "datasets/coco.names"
    gate = root / "data/models/score_gate/score_gate_reducemax_f32.bmodel"
    worker = [
        str(root / "demos/hdmi_wall/build/hdmi_wall.pcie"),
        "--input", str(source), "--streams", str(args.streams), "--device", str(args.device),
        "--bmodel", str(model), "--classnames", str(classes), "--warmup", "3",
        "--duration", str(args.duration), "--window", str(min(10, args.duration)), "--local-eof", "loop",
        "--output-buffer", "baseline", "--score-gate", args.score_gate,
        "--record-mode", args.record_mode,
        "--prime-local-decoders", args.prime_local_decoders,
        "--gate-merge-budget-kib", str(args.gate_merge_budget_kib),
        "--cpu-post", "selected" if args.score_gate == "on" else "dense",
        "--policy", args.policy, "--infer-fps", str(args.infer_fps),
        "--max-frame-age-ms", str(args.max_frame_age_ms),
        "--image-path", args.image_path,
    ]
    if args.score_gate == "on":
        worker.extend(["--score-gate-model", str(gate)])
    worker.extend(["--output", str(output)])
    player = [
        "/usr/bin/ffplay", "-fs", "-f", "rawvideo", "-pixel_format", "bgr24",
        "-video_size", "1920x1080", "-framerate", "10", "-an", "-i", str(output / "preview.bgr"),
    ]
    return {
        "output": str(output), "worker_command": worker, "player_command": player,
        "player_environment": player_environment(), "input": str(source), "model": str(model),
        "classnames": str(classes), "score_gate_model": str(gate),
        "selected_device": args.device, "streams": args.streams,
        "gate_merge_budget_kib": args.gate_merge_budget_kib,
        "record_mode": args.record_mode,
        "prime_local_decoders": args.prime_local_decoders,
        "policy": args.policy, "infer_fps": args.infer_fps, "max_frame_age_ms": args.max_frame_age_ms,
        "image_path": args.image_path,
    }


def required_files(args: argparse.Namespace, plan: dict) -> list[str]:
    required = [plan["worker_command"][0], plan["player_command"][0], plan["model"], plan["classnames"]]
    if not live_source(plan["input"]):
        required.append(plan["input"])
    if args.score_gate == "on":
        required.append(plan["score_gate_model"])
    return required


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def process_identity(pid: int) -> dict | None:
    """Read identity twice around metadata to reject an exit/reuse during capture."""
    proc = Path("/proc") / str(pid)
    try:
        before = (proc / "stat").read_text()
        fields = before[before.rfind(")") + 2:].split()
        if fields[0] == "Z":
            return None
        command_bytes = (proc / "cmdline").read_bytes()
        cmdline = command_bytes.split(b"\0")
        if cmdline[-1] == b"":
            cmdline.pop()
        if not cmdline or not cmdline[0]:
            return None  # /proc can briefly expose an empty argv during exec.
        executable = os.readlink(proc / "exe")
        if ((proc / "cmdline").read_bytes() != command_bytes or
                os.readlink(proc / "exe") != executable):
            return None
        after = (proc / "stat").read_text()
        final_fields = after[after.rfind(")") + 2:].split()
        if fields[19] != final_fields[19] or final_fields[0] == "Z":
            return None
        return {
            "pid": pid, "pgid": int(fields[2]), "session": int(fields[3]),
            "start_ticks": fields[19], "boot_id": boot_id(), "exe": executable,
            "cmdline": [os.fsdecode(part) for part in cmdline],
        }
    except (OSError, ValueError, IndexError):
        return None


def capture_child_identity(process: subprocess.Popen, command: list[str]) -> dict | None:
    """Wait briefly for the owned child to expose its complete post-exec identity."""
    deadline = time.monotonic() + IDENTITY_TIMEOUT
    executable = os.path.realpath(command[0])
    while True:
        if process.poll() is not None:
            return None  # Let supervision preserve an early child's exit code.
        identity = process_identity(process.pid)
        if (identity is not None and identity["cmdline"] == command and
                identity["exe"] == executable and
                identity["pid"] == identity["pgid"] == identity["session"] == process.pid):
            return identity
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out capturing complete process identity for child {process.pid}: {command[0]}")
        time.sleep(0.02)


def is_same_process(identity: dict | None) -> bool:
    if not isinstance(identity, dict) or not isinstance(identity.get("pid"), int):
        return False
    current = process_identity(identity["pid"])
    keys = ("pid", "pgid", "session", "start_ticks", "boot_id", "exe", "cmdline")
    return current is not None and all(current.get(key) == identity.get(key) for key in keys)


def send_verified(identity: dict | None, signum: int, group: bool = False) -> bool:
    if not is_same_process(identity):
        return False
    assert identity is not None
    try:
        if group:
            if identity["pgid"] != identity["pid"] or identity["session"] != identity["pid"]:
                raise RuntimeError("Refusing to signal a process group not owned by a child session")
            os.killpg(identity["pgid"], signum)
        else:
            os.kill(identity["pid"], signum)
        return True
    except ProcessLookupError:
        return False


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def other_loads() -> list[dict]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = entry.joinpath("cmdline").read_bytes().split(b"\0")
            executable = os.readlink(entry / "exe").removesuffix(" (deleted)")
            name = Path(executable).name
            argv0 = Path(os.fsdecode(command[0])).name if command else ""
            if name in LOAD_NAMES or argv0 in LOAD_NAMES:
                found.append({"pid": int(entry.name), "exe": executable})
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return found


def device_snapshot() -> list[dict]:
    """Record kernel links as observed; do not infer SDK device-id/PCI mappings."""
    result = []
    for device in sorted(Path("/dev").glob("bm*")):
        record = {"path": str(device)}
        try:
            info = device.stat()
            record["resolved_path"] = str(device.resolve())
            if stat.S_ISCHR(info.st_mode):
                record["major"] = os.major(info.st_rdev)
                record["minor"] = os.minor(info.st_rdev)
                sysfs = Path(f"/sys/dev/char/{record['major']}:{record['minor']}")
                record["sysfs"] = str(sysfs.resolve()) if sysfs.exists() else None
                record["device_link"] = str((sysfs / "device").resolve()) if (sysfs / "device").exists() else None
        except OSError as exc:
            record["error"] = str(exc)
        result.append(record)
    # Some bm-sophon kernels expose the BDF only through a class device link.
    for class_dir in sorted(Path("/sys/class").glob("bm*")):
        for entry in sorted(class_dir.iterdir()):
            result.append({"class_entry": str(entry), "resolved_path": str(entry.resolve()),
                           "device_link": str((entry / "device").resolve()) if (entry / "device").exists() else None})
    return result


def stop_children(children: list[tuple[subprocess.Popen, dict | None]]) -> None:
    """Stop in dependency order: keep the player alive while the worker drains."""
    def signal_owned(process: subprocess.Popen, identity: dict | None, signum: int) -> None:
        if not send_verified(identity, signum, group=True):
            # These Popen objects belong to this launcher, unlike identities read
            # by --stop. If exec-time capture failed, the owned process still
            # needs cleanup; do not weaken group or persisted identity checks.
            if signum == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()

    for process, identity in children:
        if process.poll() is None:
            signal_owned(process, identity, signal.SIGTERM)
        try:
            process.wait(timeout=STOP_GRACE)
            continue
        except subprocess.TimeoutExpired:
            signal_owned(process, identity, signal.SIGKILL)
        try:
            process.wait(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"Process {process.pid} did not exit within the cleanup deadline", file=sys.stderr)


def exit_status(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def stop_latest(parent: Path) -> int:
    latest = json.loads((parent / "latest.json").read_text(encoding="utf-8"))
    output = Path(latest["output"]).resolve()
    if output.parent != parent.resolve():
        raise RuntimeError("latest.json points outside this launcher's results directory")
    metadata = json.loads((output / "run.json").read_text(encoding="utf-8"))
    if metadata.get("launcher_kind") != "bm1684x-hdmi-wall-v1":
        raise RuntimeError("Unrecognized run.json; refusing to stop processes")
    launcher = metadata.get("launcher")
    child_identities = [metadata.get("worker"), metadata.get("player")]
    sent = send_verified(launcher, signal.SIGTERM)
    # The launcher stops worker, then player. Allow both bounded cleanup phases
    # before falling back, or this command could disconnect the player's FIFO
    # while the launcher is still waiting for the worker to finish its summary.
    deadline = time.monotonic() + len(child_identities) * (STOP_GRACE + REAP_TIMEOUT) + 2
    while sent and is_same_process(launcher) and time.monotonic() < deadline:
        time.sleep(0.1)
    # Re-read: a player may have started between the first snapshot and TERM.
    metadata = json.loads((output / "run.json").read_text(encoding="utf-8"))
    child_identities = [metadata.get("worker"), metadata.get("player")]
    for identity in child_identities:
        sent = send_verified(identity, signal.SIGTERM, group=True) or sent
        deadline = time.monotonic() + STOP_GRACE
        while is_same_process(identity) and time.monotonic() < deadline:
            time.sleep(0.1)
        send_verified(identity, signal.SIGKILL, group=True)
        deadline = time.monotonic() + REAP_TIMEOUT
        while is_same_process(identity) and time.monotonic() < deadline:
            time.sleep(0.1)
    if sent and is_same_process(launcher):
        send_verified(launcher, signal.SIGKILL)
    if any(is_same_process(item) for item in [launcher, *child_identities]):
        print("Some verified processes remain alive", file=sys.stderr)
        return 1
    print(f"Stopped verified HDMI wall processes: {output}" if sent else f"No matching live processes: {output}")
    return 0


def run(args: argparse.Namespace, plan: dict) -> int:
    import fcntl  # Linux only; --dry-run works on development hosts.

    parent = Path(plan["output"]).parent
    parent.mkdir(parents=True, exist_ok=True)
    with (parent / "launcher.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("An HDMI wall launcher already holds the lock; use --stop first") from exc
        loads = other_loads()
        if loads:
            raise RuntimeError("Existing accelerator load detected; refusing to start: " + json.dumps(loads))
        for name in required_files(args, plan):
            if not Path(name).is_file():
                raise RuntimeError(f"Required file is missing: {name}")
        for executable in (plan["worker_command"][0], plan["player_command"][0]):
            if not os.access(executable, os.X_OK):
                raise RuntimeError(f"File is not executable: {executable}")
        output = Path(plan["output"])
        if output.exists():
            raise RuntimeError(f"Output must be a fresh directory: {output}")
        metadata = dict(plan, launcher_kind="bm1684x-hdmi-wall-v1", launcher=process_identity(os.getpid()),
                        devices=device_snapshot(), started_at=dt.datetime.now(dt.timezone.utc).isoformat(), status="starting")
        pending_log = parent / ("." + output.name + ".worker.log")
        children = []
        signals = []
        previous_handlers = {}
        player_log = None
        worker = None
        player = None
        published = False
        result = 1

        def requested_stop(signum: int, _frame: object) -> None:
            signals.append(signum)

        def publish() -> None:
            nonlocal published
            if output.is_dir():
                if pending_log.exists():
                    os.replace(pending_log, output / "worker.log")
                save_json(output / "run.json", metadata)
                if not published:
                    save_json(parent / "latest.json", {"output": str(output), "run_id": output.name})
                    published = True

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, requested_stop)
        try:
            with pending_log.open("xb", buffering=0) as worker_log:
                print("Worker: " + shlex.join(plan["worker_command"]), flush=True)
                worker = subprocess.Popen(plan["worker_command"], cwd=args.root, stdout=worker_log,
                                          stderr=subprocess.STDOUT, start_new_session=True)
                children.append((worker, None))
                metadata["worker"] = capture_child_identity(worker, plan["worker_command"])
                children[-1] = (worker, metadata["worker"])
                deadline = time.monotonic() + args.fifo_timeout
                fifo = output / "preview.bgr"
                while True:
                    publish()
                    if signals:
                        result = 128 + signals[0]
                        break
                    worker_code = worker.poll()
                    if worker_code is not None:
                        result = exit_status(worker_code)
                        metadata["exit_reason"] = "worker exited before player startup"
                        break
                    try:
                        fifo_ready = stat.S_ISFIFO(fifo.stat().st_mode)
                    except FileNotFoundError:
                        fifo_ready = False
                    if fifo_ready:
                        player_log = (output / "player.log").open("xb", buffering=0)
                        env = dict(os.environ, **plan["player_environment"])
                        print("Player: " + shlex.join(plan["player_command"]), flush=True)
                        player = subprocess.Popen(plan["player_command"], cwd=args.root, env=env,
                                                  stdout=player_log, stderr=subprocess.STDOUT, start_new_session=True)
                        children.append((player, None))
                        metadata["player"] = capture_child_identity(player, plan["player_command"])
                        children[-1] = (player, metadata["player"])
                        metadata["status"] = "running"
                        publish()
                        print(f"HDMI wall is running; logs: {output}", flush=True)
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Timed out waiting for worker FIFO: {fifo}")
                    time.sleep(0.1)
                if player is not None:
                    while True:
                        if signals:
                            result = 128 + signals[0]
                            metadata["exit_reason"] = f"launcher received signal {signals[0]}"
                            break
                        worker_code = worker.poll()
                        player_code = player.poll()
                        if worker_code is not None or player_code is not None:
                            # Preserve an already observed failure, even if its peer also exited.
                            observed = [code for code in (worker_code, player_code) if code is not None]
                            result = exit_status(next((code for code in observed if code != 0), observed[0]))
                            metadata["exit_reason"] = "worker exited" if worker_code is not None else "player exited"
                            break
                        time.sleep(0.1)
        except Exception as exc:
            metadata["error"] = str(exc)
            print(f"HDMI wall error: {exc}", file=sys.stderr)
            result = 1
        finally:
            stop_children(children)
            if player_log is not None:
                player_log.close()
            metadata.update(status="stopped", exit_status=result,
                            worker_returncode=worker.poll() if worker else None,
                            player_returncode=player.poll() if player else None,
                            finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
            try:
                publish()
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)
            if not published:
                print(f"Worker did not create its output directory; startup log: {pending_log}", file=sys.stderr)
        return result


def main() -> int:
    args = arguments()
    plan = build_plan(args)
    if args.dry_run:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    if not sys.platform.startswith("linux"):
        print("Launching and stopping require the Linux board; use --dry-run here.", file=sys.stderr)
        return 2
    try:
        if args.stop:
            return stop_latest(Path(args.root) / "data/results/hdmi-wall")
        return run(args, plan)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"HDMI wall: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
