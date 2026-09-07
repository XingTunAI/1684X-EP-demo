#!/usr/bin/env python3
"""Run the paced decode + YOLOv8 baseline on RK3588 and save reports."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    os.chdir(ROOT)
    supplied = sys.argv[1:]
    defaults = ['--device', '1', '--mode', 'analysis', '--steps', '1,2,4,8',
                '--warmup', '30', '--duration', '120', '--window', '60', '--stall-timeout', '60',
                '--bmodel', 'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel']
    if not any(x.split('=')[0] in ('--input', '--inputs-file') for x in supplied):
        defaults += ['--input', 'datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4']
    run = runpy.run_path(str(ROOT / 'src/single_card_pipeline/run.py'))
    raise SystemExit(run['main'](defaults + supplied))
