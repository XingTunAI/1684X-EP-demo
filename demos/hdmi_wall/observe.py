#!/usr/bin/env python3
"""Observe decoder output with inference on or off, keeping all channels active.

Run the decoder baseline and inference-loaded case separately with identical
devices, stream count, duration and comparison-image sampling parameters.
"""

from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath
import sys

import multi_run
import showcase


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=multi_run.single.DEFAULT_ROOT,
                        help="Absolute Linux repository path; required for Windows --dry-run.")
    parser.add_argument("--devices", type=showcase.selected_devices, default="auto",
                        help="auto discovers 1..4 real sysfs devices; or give explicit IDs such as 0,1.")
    parser.add_argument("--streams", type=multi_run.single.positive, default=32,
                        help="Background decoders per device, 1..32; default 32, independent of selected comparison views.")
    parser.add_argument("--duration", type=multi_run.single.positive, default=300,
                        help="Formal duration in seconds, plus 3s warmup; default 300.")
    parser.add_argument("--inference", choices=("on", "off"), default="on",
                        help="on loads YOLOv8s and score gate; off runs a genuine decoder-only baseline.")
    parser.add_argument("--compare-streams", default="0,1",
                        help="1..4 distinct zero-based stream IDs shown for comparison; default 0,1.")
    parser.add_argument("--observe-preview-fps", type=multi_run.single.observation_preview_fps, default=5.0,
                        help="Decoded-image sampling cap per selected stream, (0,10] FPS; default 5.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and configuration without writing files or starting processes.")
    parser.add_argument("--stop", action="store_true", help="Stop the latest verified multi-device run without discovery, preflight or video preparation.")
    args = parser.parse_args(argv)
    if args.root is None or not PurePosixPath(args.root).is_absolute():
        parser.error("--root must be an absolute Linux path")
    if args.stop and args.dry_run:
        parser.error("--stop and --dry-run cannot be combined")
    if args.streams > 32:
        parser.error("--streams must be between 1 and 32 per device")
    args.observe_decode = "on"
    args.policy = "latest"
    args.score_gate = "on" if args.inference == "on" else "off"
    # Reuse the established preparation/supervisor plan and hardware mapping.
    args.mode = "showcase"
    args.profile = None
    args.telemetry_interval = 5.0
    multi_run.single.validate_observation(parser, args, [args.streams])
    return args


def build_plan(args) -> dict:
    plan = showcase.build_plan(args)
    if args.stop:
        return plan
    configuration = plan["device_configuration"]
    links = configuration["context"]["pcie_devices"]
    budget_sources = {}
    for device, options in configuration["devices"].items():
        options["streams"] = args.streams
        if args.inference == "off":
            options["gate_merge_budget_kib"] = 0
        budget_sources[device] = ("inference_disabled" if args.inference == "off" else
                                  configuration["context"]["profile_selection"][device]["default_source"])
    experiment = "inference_loaded" if args.inference == "on" else "decoder_only_baseline"
    configuration["context"] = {
        "entrypoint": "observe", "experiment": experiment,
        "duration_seconds": args.duration, "warmup_seconds": 3, "material_seconds": plan["material_seconds"],
        "input": plan["input"], "record_mode": "summary", "prime_local_decoders": "on",
        "observe_decode": "on", "inference": args.inference, "compare_stream_ids": args.compare_stream_ids,
        "observe_preview_fps": args.observe_preview_fps, "pcie_devices": links,
        "budget_sources": budget_sources,
        "note": "All configured decoders remain active; inference on processes all configured channels. Selected decoded-image views do not reduce background load. Preview/CPU overhead is included; TPU utilization is measured, not guaranteed. Local repeated footage is not independent-camera acceptance.",
    }
    old_config = PurePosixPath(plan["device_config_path"])
    new_config = PurePosixPath(args.root) / "data/results/hdmi-observe" / old_config.parent.name / old_config.name
    plan["device_config_path"] = str(new_config)
    command = plan["run_command"]
    command[command.index("--device-config") + 1] = str(new_config)
    command[command.index("--score-gate") + 1] = args.score_gate
    command.extend(["--observe-decode", "on", "--inference", args.inference,
                    "--compare-streams", args.compare_streams, "--observe-preview-fps", str(args.observe_preview_fps)])
    plan.update(mode=experiment, streams_per_device=args.streams, observe_decode="on", inference=args.inference,
                compare_streams=args.compare_streams, compare_stream_ids=args.compare_stream_ids,
                observe_preview_fps=args.observe_preview_fps)
    return plan


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
        return showcase.execute(args, plan)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print("HDMI decoder observation: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
