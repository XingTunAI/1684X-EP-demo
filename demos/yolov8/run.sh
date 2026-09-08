#!/usr/bin/env bash
set -euo pipefail
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${DEMO_DIR}/run.py" "$@"
