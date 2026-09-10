#!/usr/bin/env bash
# Compare decoder-only and inference-loaded runs while keeping all channels active.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec python3 "$SCRIPT_DIR/observe.py" --root "$PROJECT_ROOT" "$@"
