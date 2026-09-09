#!/usr/bin/env python3
"""Repeat the measured full-load HDMI baseline: device 1, 30 streams, YOLOv8s.

Prepares a sufficiently long local copy-loop video, then replaces this process
with the existing multi-device launcher. Display/authentication stay caller-set.
This preset does not promise the same capacity or latency at other stream counts.
"""

import argparse
import json
import os
from pathlib import PurePosixPath
import subprocess
import sys

import multi_run


FIXED_OPTIONS = ["--model", "s", "--score-gate", "on", "--policy", "latest",
                 "--infer-fps", "0", "--max-frame-age-ms", "250", "--image-path", "auto"]


def material_seconds(duration):
    return ((duration + 3 + 60 + 599) // 600) * 600


def multi_options(args):
    if args.stop:
        return ["--root", args.root, "--stop"]
    source = PurePosixPath(args.root) / "data/inputs" / f"hdmi_wall_demo_loop_{material_seconds(args.duration)}s.mp4"
    return ["--root", args.root, "--devices", ",".join(str(device) for device in args.devices),
            "--streams", str(args.streams), "--duration", str(args.duration), "--input", str(source),
            "--gate-merge-budget-kib", str(args.gate_merge_budget_kib),
            "--prime-local-decoders", args.prime_local_decoders,
            *FIXED_OPTIONS]


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=multi_run.single.DEFAULT_ROOT,
                        help="Absolute Linux repository directory; required for Windows --dry-run.")
    parser.add_argument("--devices", type=multi_run.device_ids, default=[1], help="1..4 accelerator IDs; default: 1.")
    parser.add_argument("--streams", type=multi_run.single.positive, default=30,
                        help="Channels per device, 1..32; measured baseline: 30.")
    parser.add_argument("--duration", type=multi_run.single.positive, default=300,
                        help="Formal measurement duration in seconds; default: 300, plus 3s warmup.")
    parser.add_argument("--gate-merge-budget-kib", type=multi_run.single.gate_merge_budget_kib, default=0,
                        help="Extra score-gate output-read budget, 0..1024 KiB; default 0 preserves the measured baseline.")
    parser.add_argument("--prime-local-decoders", choices=("off", "on"), default="off",
                        help="Experimental local decoder priming; default off preserves the measured baseline.")
    parser.add_argument("--dry-run", action="store_true", help="Print exact preparation and launch commands without accessing board files.")
    parser.add_argument("--stop", action="store_true", help="Stop the latest verified multi-device run without preparing any video.")
    args = parser.parse_args(argv)
    if args.root is None or not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path")
    if args.streams > 32:
        parser.error("--streams must be between 1 and 32 per device")
    # Keep validation aligned with the launcher, including --stop/--dry-run.
    multi_run.arguments(multi_options(args) + (["--dry-run"] if args.dry_run else []))
    return args


def build_plan(args):
    root = PurePosixPath(args.root)
    python = sys.executable if sys.platform.startswith("linux") else "/usr/bin/python3"
    run_command = [python, str(root / "demos/hdmi_wall/multi_run.py"), *multi_options(args)]
    if args.stop:
        return {"prepare_command": None, "run_command": run_command, "stop": True}
    seconds = material_seconds(args.duration)
    return {"prepare_command": [python, str(root / "scripts/prepare_hdmi_loop.py"), "--root", args.root,
                                 "--seconds", str(seconds)],
            "run_command": run_command, "stop": False, "devices": args.devices,
            "streams_per_device": args.streams, "duration_seconds": args.duration,
            "gate_merge_budget_kib": args.gate_merge_budget_kib,
            "prime_local_decoders": args.prime_local_decoders,
            "warmup_seconds": 3, "material_seconds": seconds,
            "input": str(root / "data/inputs" / f"hdmi_wall_demo_loop_{seconds}s.mp4"),
            "note": "Fixed YOLOv8s/gate-on/latest/unlimited inference/250ms admission age/auto image path; repeated local footage, caller-provided display environment."}


def execute(args, plan):
    if plan["prepare_command"] is not None:
        prepared = subprocess.run(plan["prepare_command"], cwd=args.root, check=False)
        if prepared.returncode:
            return multi_run.single.exit_status(prepared.returncode)
    # The established launcher owns child processes, signal handling and --stop.
    # exec preserves its exit status and adds no new background supervisor.
    os.execv(plan["run_command"][0], plan["run_command"])
    return 0  # Reached only by test doubles; successful exec does not return.


def main(argv=None):
    args = arguments(argv)
    plan = build_plan(args)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not sys.platform.startswith("linux"):
        print("Running and stopping require the Linux board; use --dry-run on this host.", file=sys.stderr)
        return 2
    try:
        return execute(args, plan)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print("HDMI benchmark: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
