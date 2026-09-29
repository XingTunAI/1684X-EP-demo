#!/usr/bin/env python3
"""Run a 20-minute HDMI showcase or three-hour stress session on the Linux board.

Every selected accelerator runs concurrently, including hidden pages. Showcase
prefers 8 channels per card; stress uses a fixed 32-channel load per card.
Neither mode guarantees TPU utilization. Caller-provided DISPLAY/XAUTHORITY
remain intact. Local repeated footage does not validate independent cameras.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import hashlib
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
DEFAULT_CARD_PROFILE = {"streams": 8, "gate_merge_budget_kib": 64}
DEFAULT_LINK_PROFILES: dict[tuple[int, int], dict] = {
    (2, 1): {"streams": 8, "gate_merge_budget_kib": 64},
    (3, 1): {"streams": 8, "gate_merge_budget_kib": 64},
    (3, 2): {"streams": 8, "gate_merge_budget_kib": 64},
}
STRESS_LINK_PROFILES: dict[tuple[int, int], dict] = {
    (2, 1): {"streams": 32, "gate_merge_budget_kib": 64},
    (3, 2): {"streams": 32, "gate_merge_budget_kib": 64},
}
MODE_GOALS = {"showcase": "Display 8 channels per card with linear decoding, 8 extra buffers and uncapped preview submissions; actual display continuity requires measurement.",
              "stress": "Measure a fixed 32-channel load per card with preview capped at 3 FPS; require per-channel continuity and complete accounting, not TPU utilization alone."}
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
    parser.add_argument("--duration", type=multi_run.single.positive, default=None,
                        help="Formal seconds plus 3s warmup; default: showcase 1200 (20min), stress 10800 (3h).")
    parser.add_argument("--mode", choices=("showcase", "stress"), default="showcase",
                        help="showcase prefers 8 channels/card; stress uses 32 channels/card with preview capped at 3 FPS. Both retain every card and HDMI preview.")
    parser.add_argument("--input", help="Local video to repeat without re-encoding; omitted uses the original 1080p24 demo.")
    parser.add_argument("--streams", type=multi_run.single.positive, help="Channels per device (1..32); per-device profile overrides win.")
    multi_run.single.add_pipeline_arguments(parser, decoder="linear")
    parser.set_defaults(wall_fps=None, display_fps=None, local_eof=None)
    parser.add_argument("--preview-fps", type=multi_run.single.detection_preview_fps, default=None,
                        help="Per-stream preview cap; default -1 (uncapped) in showcase, 3 in stress. Inference is uncapped.")
    parser.add_argument("--profile", help='JSON with devices overrides, e.g. {"devices":{"0":{"streams":20,"gate_merge_budget_kib":64}}}.')
    parser.add_argument("--telemetry-interval", type=multi_run.single.nonnegative_finite, default=5,
                        help="Per-device bm-smi sampling interval, seconds; default: 5; 0 disables.")
    parser.add_argument("--dry-run", action="store_true", help="Print exact commands/configuration without writing files or starting processes.")
    parser.add_argument("--stop", action="store_true", help="Stop the latest verified multi-device run, without probing hardware or preparing video.")
    args = parser.parse_args(argv)
    if args.duration is None:
        args.duration = 1200 if args.mode == "showcase" else 10800
    if args.root is None or not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path")
    if args.streams is not None and args.streams > 32:
        parser.error("--streams must be between 1 and 32")
    if args.input and "://" in args.input:
        parser.error("--input must be a local video path")
    if args.wall_fps is not None and args.wall_fps > 120:
        parser.error("--wall-fps must be between 0 and 120")
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
        fallback = DEFAULT_CARD_PROFILE if args.mode == "showcase" else {"streams": 32, "gate_merge_budget_kib": 64}
        defaults = link_profiles.get(link_key, fallback)
        defaults = dict(defaults)
        if args.streams is not None:
            defaults["streams"] = args.streams
        options[str(device)] = dict(defaults, **overrides.get(str(device), {}))
        profile_selection[str(device)] = {"default_source": "short_test_link_profile" if link_key in link_profiles else "unvalidated_fallback",
                                          "explicit_overrides": overrides.get(str(device), {})}
    # Validate final options through the same contract used by the supervisor.
    options = multi_run.normalize_device_overrides(options)
    seconds = benchmark.material_seconds(args.duration)
    source = root / "data/inputs" / f"hdmi_wall_demo_loop_{seconds}s.mp4"
    original = str(root / PurePosixPath(args.input)) if args.input else None
    prepare = [python, str(root / "scripts/prepare_hdmi_loop.py"), "--root", args.root, "--seconds", str(seconds)]
    if original:
        # Distinct inputs cannot reuse/overwrite the default video's cache.
        key = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
        source = root / "data/inputs" / f"hdmi_wall_{key}_loop_{seconds}s.mp4"
        prepare.extend(["--input", original, "--output", str(source)])
    preview_fps = args.preview_fps if args.preview_fps is not None else (-1.0 if args.mode == "showcase" else 3.0)
    display_fps = args.display_fps if args.display_fps is not None else (30 if args.mode == "showcase" else 10)
    wall_fps = args.wall_fps if args.wall_fps is not None else 0.0
    local_eof = args.local_eof or ("loop" if args.mode == "showcase" else "fail")
    pipeline = ["--local-eof", local_eof,"--decoder", args.decoder, "--decoder-buffers", str(args.decoder_buffers),
                "--retrieve-every", str(args.retrieve_every), "--output-buffer", "reuse",
                "--preview-fps", str(preview_fps), "--wall-fps", str(wall_fps),
                "--display-fps", str(display_fps)]
    config_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    config_path = root / "data/results/hdmi-showcase" / config_id / "device-config.json"
    context = {"entrypoint": "showcase", "mode": args.mode, "mode_goal": MODE_GOALS[args.mode], "duration_seconds": args.duration,
               "warmup_seconds": 3, "material_seconds": seconds, "record_mode": "summary",
               "local_eof": local_eof, "source_input": original, "decoder": args.decoder, "decoder_buffers": args.decoder_buffers,
               "preview_fps": preview_fps, "display_fps": display_fps, "wall_fps": wall_fps, "retrieve_every": args.retrieve_every,
               "prime_local_decoders": PRIME_LOCAL_DECODERS,
               "pcie_devices": links, "profile_path": str(profile_path) if profile_path else None,
               "profile_context": profile_context, "profile_status": "configured_not_capacity_certified",
               "profile_validation_scope": "Settings selected from short board comparisons; concurrent and four-hour performance must be assessed from this run's summaries and telemetry. No TPU utilization guarantee.",
               "default_profile": fallback,
               "default_link_profiles": {f"gen{generation}_x{width}": options for (generation, width), options in link_profiles.items()},
               "profile_selection": profile_selection,
               "note": "All selected devices run concurrently. Both modes retain preview. Repeated local video is not independent-camera live acceptance."}
    return {"stop": False, "devices": devices, "duration_seconds": args.duration,
            "material_seconds": seconds, "input": str(source), "mode": args.mode,
            "record_mode": "summary", "telemetry_interval_seconds": args.telemetry_interval,
            "prime_local_decoders": PRIME_LOCAL_DECODERS,
            "device_config_path": str(config_path), "device_configuration": {"devices": options, "context": context},
            "prepare_command": prepare,
            "run_command": [*launcher, "--root", args.root, "--devices", ",".join(map(str, devices)),
                            "--duration", str(args.duration), "--input", str(source),
                            "--device-config", str(config_path), "--telemetry-interval", str(args.telemetry_interval),
                            *pipeline, *FIXED_OPTIONS]}


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
