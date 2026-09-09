#!/usr/bin/env bash
# Each selected device runs 1..32 channels concurrently; buttons switch HDMI pages.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec python3 "$SCRIPT_DIR/multi_run.py" --root "$PROJECT_ROOT" "$@"
