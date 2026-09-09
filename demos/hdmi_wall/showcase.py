#!/usr/bin/env python3
"""Run a four-hour multi-card HDMI showcase or stress session on the Linux board.

Every selected accelerator runs concurrently, including hidden pages. Showcase
prefers 32 channels per card; stress selects per-link load-oriented settings.
Neither mode guarantees TPU utilization. Caller-provided DISPLAY/XAUTHORITY
remain intact. Local repeated footage does not validate independent cameras.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import uuid

import benchmark
import multi_run


SYSFS_CLASS = Path("/sys/class/bm-sophon")
PCI_ADDRESS = re.compile(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]")
GENERATION = {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}
# Candidate settings from short board comparisons, not four-hour certification.
# Unknown links retain an explicitly unvalidated fallback. Keys below are the
# actual (generation, width), never device IDs; explicit card options win.
DEFAULT_CARD_PROFILE = {"streams": 32, "gate_merge_budget_kib": 64}
DEFAULT_LINK_PROFILES: dict[tuple[int, int], dict] = {
    (2, 1): {"streams": 32, "gate_merge_budget_kib": 128},
    (3, 2): {"streams": 32, "gate_merge_budget_kib": 64},
}
STRESS_LINK_PROFILES: dict[tuple[int, int], dict] = {
    (2, 1): {"streams": 20, "gate_merge_budget_kib": 64},
    (3, 2): {"streams": 32, "gate_merge_budget_kib": 64},
}
MODE_GOALS = {"showcase": "Prefer 32 visible channels per device while all selected devices keep running.",
              "stress": "Prefer load-oriented per-link settings while all selected devices keep running; full TPU utilization is not guaranteed."}
PRIME_LOCAL_DECODERS = "on"
FIXED_OPTIONS = [*benchmark.FIXED_OPTIONS, "--record-mode", "summary", "--prime-local-decoders", PRIME_LOCAL_DECODERS]


def selected_devices(value: str):
    return "auto" if value == "auto" else multi_run.device_ids(value)


def device_link(device: int, sysfs_class: Path | None = None) -> dict:
    entry = (sysfs_class or SYSFS_CLASS) / f"bm-sophon{device}"
    resolved = entry.resolve(strict=True)
    endpoint = next((parent for parent in (resolved, *resolved.parents)
                     if PCI_ADDRESS.fullmatch(parent.name)), None)
    if endpoint is None:
        raise ValueError(f"No PCI endpoint ancestor for {entry}: {resolved}")
    speed_text = (endpoint / "current_link_speed").read_text(encoding="ascii").strip()
    width_text = (endpoint / "current_link_width").read_text(encoding="ascii").strip()
    speed_match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s+GT/s(?:\s+PCIe)?", speed_text)
    if speed_match is None or not width_text.isdecimal() or int(width_text) < 1:
        raise ValueError(f"Invalid current PCIe link for device {device}: {speed_text!r}, {width_text!r}")
    speed = float(speed_match[1])
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(f"Invalid current PCIe link speed for device {device}: {speed_text!r}")
    generation = GENERATION.get(speed)
    width = int(width_text)
    return {"device": device, "available": True, "sysfs_class_path": str(entry),
            "resolved_device_path": str(resolved), "pci_endpoint_path": str(endpoint),
            "pci_address": endpoint.name, "current_link_speed": speed_text,
            "current_link_width": width, "speed_gts": speed, "generation": generation,
            "label": f"PCIe {generation}.0 x{width}" if generation else f"PCIe {speed:g} GT/s x{width}"}


def discover_devices(sysfs_class: Path | None = None) -> list[int]:
    directory = sysfs_class or SYSFS_CLASS
    devices = sorted(int(match[1]) for entry in directory.iterdir()
                     if (match := re.fullmatch(r"bm-sophon(0|[1-9][0-9]*)", entry.name)))
    if not 1 <= len(devices) <= 4:
        raise ValueError(f"Found {len(devices)} devices in {directory}; select 1..4 explicitly with --devices")
    return devices


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=multi_run.single.DEFAULT_ROOT,
                        help="Absolute Linux repository path; required for Windows --dry-run.")
    parser.add_argument("--devices", type=selected_devices, default="auto",
                        help="auto discovers 1..4 sysfs devices; or provide IDs such as 0,1.")
    parser.add_argument("--duration", type=multi_run.single.positive, default=14400,
                        help="Formal run duration in seconds, plus 3s warmup; default: 14400 (4h).")
    parser.add_argument("--mode", choices=("showcase", "stress"), default="showcase",
                        help="showcase prefers 32 channels/card; stress uses per-link load-oriented settings. Both retain every card and HDMI preview.")
    parser.add_argument("--profile", help='JSON with devices overrides, e.g. {"devices":{"0":{"streams":20,"gate_merge_budget_kib":64}}}.')
    parser.add_argument("--telemetry-interval", type=multi_run.single.nonnegative_finite, default=5,
                        help="Per-device bm-smi sampling interval, seconds; default: 5; 0 disables.")
    parser.add_argument("--dry-run", action="store_true", help="Print exact commands/configuration without writing files or starting processes.")
    parser.add_argument("--stop", action="store_true", help="Stop the latest verified multi-device run, without probing hardware or preparing video.")
    args = parser.parse_args(argv)
    if args.root is None or not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path")
    if args.stop and args.dry_run:
        parser.error("--stop and --dry-run cannot be combined")
    return args


def build_plan(args) -> dict:
    root = PurePosixPath(args.root)
    python = sys.executable if sys.platform.startswith("linux") else "/usr/bin/python3"
    launcher = [python, str(root / "demos/hdmi_wall/multi_run.py")]
    if args.stop:
        return {"stop": True, "prepare_command": None, "run_command": [*launcher, "--root", args.root, "--stop"]}
    on_board = sys.platform.startswith("linux")
    if args.devices == "auto":
        if not on_board:
            raise ValueError("--devices auto requires the board's real sysfs; use explicit --devices for a Windows dry-run")
        devices = discover_devices()
    else:
        devices = args.devices
    links = [device_link(device) for device in devices] if on_board else [
        {"device": device, "available": False, "generation": None, "label": "PCIe unknown",
         "error": "Non-Linux dry-run: board sysfs is unavailable; no link generation is inferred from device ID."}
        for device in devices]
    overrides, profile_context = {}, {}
    profile_path = None
    if args.profile:
        profile_path = Path(args.profile)
        if not profile_path.is_absolute():
            profile_path = Path(args.root) / profile_path
        overrides, profile_context = multi_run.read_device_configuration(profile_path)
    options = {}
    profile_selection = {}
    link_profiles = DEFAULT_LINK_PROFILES if args.mode == "showcase" else STRESS_LINK_PROFILES
    for device, link in zip(devices, links):
        link_key = (link.get("generation"), link.get("current_link_width")) if link.get("available") is True else (None, None)
        defaults = link_profiles.get(link_key, DEFAULT_CARD_PROFILE)
        options[str(device)] = dict(defaults, **overrides.get(str(device), {}))
        profile_selection[str(device)] = {"default_source": "short_test_link_profile" if link_key in link_profiles else "unvalidated_fallback",
                                          "explicit_overrides": overrides.get(str(device), {})}
    # Validate final options through the same contract used by the supervisor.
    options = multi_run.normalize_device_overrides(options)
    seconds = benchmark.material_seconds(args.duration)
    source = root / "data/inputs" / f"hdmi_wall_demo_loop_{seconds}s.mp4"
    config_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    config_path = root / "data/results/hdmi-showcase" / config_id / "device-config.json"
    context = {"entrypoint": "showcase", "mode": args.mode, "mode_goal": MODE_GOALS[args.mode], "duration_seconds": args.duration,
               "warmup_seconds": 3, "material_seconds": seconds, "record_mode": "summary",
               "prime_local_decoders": PRIME_LOCAL_DECODERS,
               "pcie_devices": links, "profile_path": str(profile_path) if profile_path else None,
               "profile_context": profile_context, "profile_status": "configured_not_capacity_certified",
               "profile_validation_scope": "Settings selected from short board comparisons; concurrent and four-hour performance must be assessed from this run's summaries and telemetry. No TPU utilization guarantee.",
               "default_profile": DEFAULT_CARD_PROFILE,
               "default_link_profiles": {f"gen{generation}_x{width}": options for (generation, width), options in link_profiles.items()},
               "profile_selection": profile_selection,
               "note": "All selected devices run concurrently. Both modes retain preview. Repeated local video is not independent-camera live acceptance."}
    return {"stop": False, "devices": devices, "duration_seconds": args.duration,
            "material_seconds": seconds, "input": str(source), "mode": args.mode,
            "record_mode": "summary", "telemetry_interval_seconds": args.telemetry_interval,
            "prime_local_decoders": PRIME_LOCAL_DECODERS,
            "device_config_path": str(config_path), "device_configuration": {"devices": options, "context": context},
            "prepare_command": [python, str(root / "scripts/prepare_hdmi_loop.py"), "--root", args.root,
                                "--seconds", str(seconds)],
            "run_command": [*launcher, "--root", args.root, "--devices", ",".join(map(str, devices)),
                            "--duration", str(args.duration), "--input", str(source),
                            "--device-config", str(config_path), "--telemetry-interval", str(args.telemetry_interval),
                            *FIXED_OPTIONS]}


def shared_lock_owner(lock_path: Path, proc_locks: Path = Path("/proc/locks")) -> int | None:
    """Inspect an existing Linux flock without opening, creating or taking it."""
    try:
        info = lock_path.stat()
    except FileNotFoundError:
        return None
    wanted = (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)
    for line in proc_locks.read_text(encoding="ascii").splitlines():
        fields = line.split()
        # Waiting lock requests contain '->' and do not own the file lock.
        if len(fields) < 8 or fields[1] != "FLOCK" or fields[3] not in ("READ", "WRITE"):
            continue
        try:
            major, minor, inode = fields[5].split(":")
            key = (int(major, 16), int(minor, 16), int(inode))
            pid = int(fields[4])
        except ValueError:
            continue
        if key == wanted and pid > 0:
            return pid
    return None


def check_existing_run(root: Path) -> None:
    """Fail before large-video hashing when a verified HDMI run already exists.

    This is a read-only early hint, not a lock acquisition. The multi-device
    supervisor still owns the final atomic lock and accelerator-load checks.
    """
    layouts = (("hdmi-wall-multi", multi_run.LAUNCHER_KIND, "showcase.sh"),
               ("hdmi-wall", "bm1684x-hdmi-wall-v1", "run.sh"))
    for directory, kind, stop_script in layouts:
        parent = root / "data/results" / directory
        try:
            latest = json.loads((parent / "latest.json").read_text(encoding="utf-8"))
            output = Path(latest["output"]).resolve()
            if output.parent != parent.resolve():
                continue
            metadata = json.loads((output / "run.json").read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or metadata.get("launcher_kind") != kind:
                continue
            identities = [metadata.get("launcher"), metadata.get("worker"), metadata.get("player"), metadata.get("viewer")]
            if isinstance(metadata.get("workers"), list):
                identities.extend(item.get("identity") for item in metadata["workers"] if isinstance(item, dict))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # Stale/partial metadata is not evidence of a live process.
        for identity in identities:
            if multi_run.single.is_same_process(identity):
                stop = ["sudo", "bash", str(root / "demos/hdmi_wall" / stop_script), "--root", str(root), "--stop"]
                raise RuntimeError(f"HDMI wall is already running: {output} (verified PID {identity['pid']}).\n"
                                   f"Video preparation has not started. Stop the existing run first with:\n  {shlex.join(stop)}")
    lock_path = root / "data/results/hdmi-wall/launcher.lock"
    try:
        owner = shared_lock_owner(lock_path)
    except OSError as exc:
        raise RuntimeError(f"Cannot check the existing HDMI lock before video preparation: {lock_path}: {exc}") from exc
    if owner is not None:
        stop = ["sudo", "bash", str(root / "demos/hdmi_wall/showcase.sh"), "--root", str(root), "--stop"]
        raise RuntimeError(f"HDMI launcher lock is already held by PID {owner}: {lock_path}.\n"
                           "The active run directory is not yet available in verified latest metadata; video preparation has not started.\n"
                           f"Once its run metadata is published, stop the multi-device run with:\n  {shlex.join(stop)}")


def execute(args, plan):
    if not plan["stop"]:
        check_existing_run(Path(args.root))
        prepared = subprocess.run(plan["prepare_command"], cwd=args.root, check=False)
        if prepared.returncode:
            return multi_run.single.exit_status(prepared.returncode)
        configuration = Path(plan["device_config_path"])
        configuration.parent.mkdir(parents=True, exist_ok=False)
        multi_run.single.save_json(configuration, plan["device_configuration"])
    os.execv(plan["run_command"][0], plan["run_command"])
    return 0  # Test doubles only; successful exec never returns.


def main(argv=None):
    args = arguments(argv)
    try:
        if not args.dry_run and not sys.platform.startswith("linux"):
            print("Running and stopping require the Linux board; use --dry-run on this host.", file=sys.stderr)
            return 2
        plan = build_plan(args)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        return execute(args, plan)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print("HDMI showcase: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
