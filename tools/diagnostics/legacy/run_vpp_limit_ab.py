#!/usr/bin/env python3
"""Compatibility entry for the historical vpp experiment."""
from preview_experiments import main as run


def main(argv=None):
    return run("vpp", argv)


if __name__ == "__main__":
    main()
