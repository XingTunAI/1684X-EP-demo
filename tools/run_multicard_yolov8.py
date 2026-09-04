#!/usr/bin/env python3
"""Launch official SOPHON YOLOv8 C++ tasks with weighted BM1684X assignment."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import re
import signal
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOPHON_DEMO_DIR = Path(
    os.environ.get("SOPHON_DEMO_DIR", PROJECT_ROOT / "third_party" / "sophon-demo")
)


@dataclass(frozen=True)
class DeviceSpec:
    dev_id: str
    weight: int
    role: str
    bdf: str
    link: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run YOLOv8 processes with weighted BM1684X PCIe assignment."
    )
    parser.add_argument(
        "--demo-dir",
        type=Path,
        default=DEFAULT_SOPHON_DEMO_DIR / "sample" / "YOLOv8_plus_det",
        help="Path to sophon-demo/sample/YOLOv8_plus_det.",
    )
    parser.add_argument(
        "--devices",
        default="1|2|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1",
        help=(
            "Comma-separated device specs id[|weight[|role[|bdf[|link]]]]. "
            "Default prefers dev_id 1, the confirmed Gen3 card."
        ),
    )
    parser.add_argument(
        "--input",
        required=False,
        help="Input video path, or comma-separated paths, relative to demo-dir or absolute.",
    )
    parser.add_argument(
        "--bmodel",
        required=False,
        help="BModel path relative to demo-dir or an absolute path.",
    )
    parser.add_argument(
        "--classnames",
        default="datasets/coco.names",
        help="Class names path relative to demo-dir or an absolute path.",
    )
    parser.add_argument("--conf-thresh", default="0.25")
    parser.add_argument("--nms-thresh", default="0.7")
    parser.add_argument(
        "--fix-card-writer",
        action="store_true",
        help=(
            "Patch the official YOLOv8 BMCV C++ sample so VideoWriter uses "
            "the same dev_id as VideoCapture. Rebuild the sample after patching."
        ),
    )
    return parser.parse_args()


def parse_device_specs(value: str) -> list[DeviceSpec]:
    specs: list[DeviceSpec] = []
    for item in value.split(","):
        parts = [part.strip() for part in item.split("|")]
        if not parts or not parts[0]:
            continue
        specs.append(
            DeviceSpec(
                dev_id=parts[0],
                weight=int(parts[1]) if len(parts) > 1 and parts[1] else 1,
                role=parts[2] if len(parts) > 2 and parts[2] else "worker",
                bdf=parts[3] if len(parts) > 3 and parts[3] else "unknown",
                link=parts[4] if len(parts) > 4 and parts[4] else "unknown",
            )
        )
    return specs


def expand_weighted_devices(specs: list[DeviceSpec]) -> list[DeviceSpec]:
    expanded: list[DeviceSpec] = []
    for spec in specs:
        expanded.extend([spec] * max(spec.weight, 1))
    return expanded


def resolve_path(base: Path, value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else base / path)


def patch_card_writer(demo_dir: Path) -> bool:
    main_cpp = demo_dir / "cpp" / "yolov8_bmcv" / "main.cpp"
    if not main_cpp.exists():
        print(f"Cannot find source file to patch: {main_cpp}", file=sys.stderr)
        return False

    source = main_cpp.read_text(encoding="utf-8")
    fixed = (
        "writer.open(output_path, output_fourcc, frameRate, cv::Size(w, h), "
        "true, dev_id);"
    )
    if fixed in source:
        print(f"Card writer fix already present: {main_cpp}")
        return True

    pattern = re.compile(
        r"writer\.open\(\s*output_path\s*,\s*output_fourcc\s*,\s*frameRate\s*,"
        r"\s*cv::Size\(w,\s*h\)\s*\)\s*;"
    )
    patched, count = pattern.subn(fixed, source, count=1)
    if count != 1:
        print(
            "Cannot patch VideoWriter automatically; expected writer.open(...) "
            f"line was not found in {main_cpp}.",
            file=sys.stderr,
        )
        return False

    backup = main_cpp.with_suffix(".cpp.bak")
    if not backup.exists():
        backup.write_text(source, encoding="utf-8")
    main_cpp.write_text(patched, encoding="utf-8")
    print(f"Patched VideoWriter dev_id binding in: {main_cpp}")
    print("Rebuild before running:")
    print(f"  cd {demo_dir / 'cpp' / 'yolov8_bmcv'}")
    print("  rm -rf build && mkdir -p build && cd build && cmake .. && make -j$(nproc)")
    return True


def main() -> int:
    args = parse_args()
    demo_dir = args.demo_dir.resolve()
    if args.fix_card_writer:
        return 0 if patch_card_writer(demo_dir) else 2

    if not args.input or not args.bmodel:
        print("--input and --bmodel are required unless --fix-card-writer is used.")
        return 2

    app = demo_dir / "cpp" / "yolov8_bmcv" / "yolov8_bmcv.pcie"
    if not app.exists():
        print(f"Cannot find C++ demo executable: {app}", file=sys.stderr)
        print("Run scripts/build_yolov8_cpp.sh first.", file=sys.stderr)
        return 2

    devices = parse_device_specs(args.devices)
    if not devices:
        print("No devices specified.", file=sys.stderr)
        return 2
    weighted_devices = expand_weighted_devices(devices)
    inputs = [item.strip() for item in args.input.split(",") if item.strip()]

    processes: list[subprocess.Popen[str]] = []

    def stop_all(signum: int, _frame: object) -> None:
        print(f"\nSignal {signum} received, stopping demo processes...")
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    for index, input_path in enumerate(inputs):
        device = weighted_devices[index % len(weighted_devices)]
        cmd = [
            str(app),
            f"--input={resolve_path(demo_dir, input_path)}",
            f"--bmodel={resolve_path(demo_dir, args.bmodel)}",
            f"--dev_id={device.dev_id}",
            f"--conf_thresh={args.conf_thresh}",
            f"--nms_thresh={args.nms_thresh}",
            f"--classnames={resolve_path(demo_dir, args.classnames)}",
        ]
        print(
            "Starting:",
            f"task={index}",
            f"dev_id={device.dev_id}",
            f"role={device.role}",
            f"bdf={device.bdf}",
            f"link={device.link}",
            " ".join(cmd),
        )
        work_dir = Path(os.environ.get("RUN_ROOT", "/tmp/1684x_ep_demo/yolov8_py_launcher")) / f"task{index}_dev{device.dev_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        processes.append(subprocess.Popen(cmd, cwd=work_dir, text=True))

    exit_code = 0
    for proc in processes:
        code = proc.wait()
        if code != 0 and exit_code == 0:
            exit_code = code

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
