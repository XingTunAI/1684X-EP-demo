#!/usr/bin/env python3
"""One-command single-card decode test and report generation (run on RK3588)."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/single_card_decode'))
from stress_decode import main

if __name__ == '__main__':
    os.chdir(ROOT)
    supplied = sys.argv[1:]
    defaults = ['--device', '0', '--steps', '8,16,24,32', '--warmup', '60',
                '--duration', '120', '--window', '60']
    if not any(x.split('=')[0] in ('--input', '--inputs-file') for x in supplied):
        defaults += ['--input', 'datasets/stress/bbb_1080p25_h264_8mbps.mp4']
    raise SystemExit(main(defaults + supplied))
