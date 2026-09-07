#!/usr/bin/env python3
"""Run the paced decode + YOLOv8 baseline on RK3588 and save reports."""
import argparse
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]

def build_arguments(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--model-preset', choices=('yolov8s', 'yolov8n'), default='yolov8s')
    preset, supplied = parser.parse_known_args(argv)
    defaults = ['--device', '1', '--mode', 'analysis', '--steps', '1,2,4,8',
                '--warmup', '30', '--duration', '120', '--window', '60', '--stall-timeout', '60',
                '--bmodel', f'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/{preset.model_preset}_int8_1b.bmodel']
    if not any(x.split('=')[0] in ('--input', '--inputs-file') for x in supplied):
        defaults += ['--input', 'datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4']
    return defaults + supplied


if __name__ == '__main__':
    os.chdir(ROOT)
    run = runpy.run_path(str(ROOT / 'src/single_card_pipeline/run.py'))
    raise SystemExit(run['main'](build_arguments(sys.argv[1:])))
