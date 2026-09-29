#!/usr/bin/env python3
"""Compatibility entry for the historical linear source stride experiment."""
from linear_source import main as run


def main(argv=None):
    return run(argv, guarded=True, allow_stride=True)


if __name__ == "__main__":
    main()
