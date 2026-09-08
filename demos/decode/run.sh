#!/usr/bin/env bash
# RK3588: automatically test decode capacity and write Markdown/CSV/JSON results.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/run.py" "$@"
