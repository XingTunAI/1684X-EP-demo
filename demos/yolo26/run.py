#!/usr/bin/env python3
"""Run yolo26 on selected cards and save per-card results."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runner = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'common/yolo_runner.py'))
    raise SystemExit(runner['main']('yolo26'))
