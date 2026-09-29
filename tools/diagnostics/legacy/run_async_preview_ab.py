#!/usr/bin/env python3
"""Compatibility entry for the historical async experiment."""
from preview_experiments import main as run


def main(argv=None):
    return run("async", argv)


if __name__ == "__main__":
    main()
