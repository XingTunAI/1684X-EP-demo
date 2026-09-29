#!/usr/bin/env python3
"""Compatibility entry for the historical linear source comparison experiment."""
from linear_source import main as run


def main(argv=None):
    return run(argv, guarded=False, allow_stride=False)


if __name__ == "__main__":
    main()
