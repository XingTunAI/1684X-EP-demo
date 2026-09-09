#!/usr/bin/env bash
# Reproduce the 30-channel YOLOv8s baseline; use --streams 32 for the next comparison.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec python3 "$SCRIPT_DIR/benchmark.py" --root "$PROJECT_ROOT" "$@"
