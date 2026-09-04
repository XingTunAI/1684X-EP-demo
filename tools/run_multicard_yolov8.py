#!/usr/bin/env python3
"""Launch the official SOPHON YOLOv8 Python demo on multiple BM1684X devices."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one YOLOv8 demo process per BM1684X PCIe device."
    )
    parser.add_argument(
        "--demo-dir",
        type=Path,
        default=Path("sophon-demo/sample/YOLOv8_plus_det"),
        help="Path to sophon-demo/sample/YOLOv8_plus_det.",
    )
    parser.add_argument(
        "--devices",
        default="0",
        help="Comma-separated BM1684X device ids, for example 0,1,2,3.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input video path relative to demo-dir or an absolute path.",
    )
    parser.add_argument(
        "--bmodel",
        required=True,
        help="BModel path relative to demo-dir or an absolute path.",
    )
    parser.add_argument("--conf-thresh", default="0.25")
    parser.add_argument("--nms-thresh", default="0.7")
    return parser.parse_args()


def resolve_path(base: Path, value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else base / path)


def main() -> int:
    args = parse_args()
    demo_dir = args.demo_dir.resolve()
    script = demo_dir / "python" / "yolov8_bmcv.py"
    if not script.exists():
        print(f"Cannot find official demo script: {script}", file=sys.stderr)
        return 2

    devices = [item.strip() for item in args.devices.split(",") if item.strip()]
    if not devices:
        print("No devices specified.", file=sys.stderr)
        return 2

    processes: list[subprocess.Popen[str]] = []

    def stop_all(signum: int, _frame: object) -> None:
        print(f"\nSignal {signum} received, stopping demo processes...")
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    for dev_id in devices:
        cmd = [
            sys.executable,
            str(script),
            "--input",
            resolve_path(demo_dir, args.input),
            "--bmodel",
            resolve_path(demo_dir, args.bmodel),
            "--dev_id",
            dev_id,
            "--conf_thresh",
            args.conf_thresh,
            "--nms_thresh",
            args.nms_thresh,
        ]
        print("Starting:", " ".join(cmd))
        processes.append(subprocess.Popen(cmd, cwd=demo_dir, text=True))

    exit_code = 0
    for proc in processes:
        code = proc.wait()
        if code != 0 and exit_code == 0:
            exit_code = code

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
