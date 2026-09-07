#!/usr/bin/env python3
"""Compatibility entry point; the maintained launcher is under scripts/."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[2] /
                      'scripts/run_single_card_decode_auto.py'), run_name='__main__')
