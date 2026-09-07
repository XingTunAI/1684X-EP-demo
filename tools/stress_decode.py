#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in src/single_card_decode."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] /
                      'src/single_card_decode/stress_decode.py'), run_name='__main__')
