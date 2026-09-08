#!/usr/bin/env bash
# Build the HDMI wall with the board's installed SOPHON SDK.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="${HDMI_BUILD_DIR:-$PROJECT_ROOT/demos/hdmi_wall/build}"
JOBS="${HDMI_BUILD_JOBS:-2}"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: bash demos/hdmi_wall/build.sh [CMake configure arguments]"
  echo "Builds demos/hdmi_wall/build/hdmi_wall.pcie using the installed SOPHON SDK."
  echo "Optional: HDMI_BUILD_DIR, HDMI_BUILD_JOBS, SOPHON_DEMO_DIR."
  exit 0
fi
if [[ ! "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
  echo "HDMI_BUILD_JOBS must be a positive integer." >&2
  exit 2
fi
cmake -S "$PROJECT_ROOT/demos/hdmi_wall" -B "$BUILD_DIR" \
  -DSOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-$PROJECT_ROOT/third_party/sophon-demo}" "$@"
cmake --build "$BUILD_DIR" -j "$JOBS"
echo "Built: $BUILD_DIR/hdmi_wall.pcie"
