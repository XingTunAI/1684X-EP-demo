#!/usr/bin/env bash
# Produce a long repeated local clip by copying its video packets, without encoding.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
exec python3 "$SCRIPT_DIR/prepare_hdmi_loop.py" --root "$PROJECT_ROOT" "$@"
