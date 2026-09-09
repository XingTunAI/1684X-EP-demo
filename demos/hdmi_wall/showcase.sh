#!/usr/bin/env bash
# Run all selected cards concurrently with four-hour summary-mode defaults.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec python3 "$SCRIPT_DIR/showcase.py" --root "$PROJECT_ROOT" "$@"
