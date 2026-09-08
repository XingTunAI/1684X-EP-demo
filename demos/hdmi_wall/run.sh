#!/usr/bin/env bash
# 1..32-channel decode + YOLO inference + HDMI wall; default 1.
# Select with --streams N or -n N; --list-streams shows supported counts.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec python3 "$SCRIPT_DIR/run.py" --root "$PROJECT_ROOT" "$@"
