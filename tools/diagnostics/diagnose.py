#!/usr/bin/env python3
"""Choose a supported diagnostic; backend --help shows its exact parameters."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {
    "compute": ("run_inference_diagnostics.py", "Resident compute / output readback; no video FPS"),
    "bandwidth": ("run_pcie_bandwidth.py", "SDK upload / download bandwidth"),
    "model": ("run_single_card_tpu_bench.py", "BMRuntime batch 1 / 4 model timing"),
    "decode": ("run_decode_capacity.py", "Independent decode with / without model load"),
    "report": ("report.py", "Read an existing HDMI run; never starts hardware work"),
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__, epilog="Use: diagnose.py COMMAND --help")
    parser.add_argument("command", choices=COMMANDS, help="; ".join(f"{k}: {v[1]}" for k, v in COMMANDS.items()))
    # Delegate backend options unchanged, including --help; never use a shell.
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    args = parser.parse_args(argv[:1])
    script = Path(__file__).with_name(COMMANDS[args.command][0])
    return subprocess.call([sys.executable, str(script), *argv[1:]], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
