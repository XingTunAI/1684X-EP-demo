#!/usr/bin/env python3
"""One-command single-card decode test and report generation (run on RK3588)."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]
main = runpy.run_path(str(Path(__file__).with_name('stress_decode.py')))['main']

if __name__ == '__main__':
    os.chdir(ROOT)
    supplied = sys.argv[1:]
    defaults = ['--device', '0', '--steps', '1', '--warmup', '60',
                '--duration', '120', '--window', '60']
    if not any(x.split('=')[0] in ('--input', '--inputs-file') for x in supplied):
        defaults += ['--input', 'third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4']
    raise SystemExit(main(defaults + supplied))
