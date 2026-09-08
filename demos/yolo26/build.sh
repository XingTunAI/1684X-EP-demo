#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${BUILD_DIR:-${SCRIPT_DIR}/build}"
BUILD_JOBS="${BUILD_JOBS:-2}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: bash demos/yolo26/build.sh [CMake configuration arguments...]

Builds demos/yolo26/build/yolo26_streams.pcie by default.
Requires CMake 3.13+, a C++11 compiler and the Sophon SDK dependencies.

Environment:
  BUILD_DIR   Build directory; relative paths are based on the repository root.
  BUILD_JOBS  Parallel build jobs (default: 2).

Examples:
  bash demos/yolo26/build.sh
  bash demos/yolo26/build.sh -DSOPHON_DEMO_DIR=/path/to/sophon-demo
  bash demos/yolo26/build.sh -DYOLO_HEADER_DIR=/path/to/utility-headers

SDK-independent parser check (use a separate build directory):
  BUILD_DIR=demos/yolo26/build/parser bash demos/yolo26/build.sh -DPARSER_ONLY=ON
  (cd demos/yolo26/build/parser && ctest --output-on-failure)

Other arguments are passed to CMake unchanged. Use BUILD_DIR to change the
output location. This script does not install SDK packages or run a device.
USAGE
  exit 0
fi

cd "${PROJECT_ROOT}"
cmake -S "${SCRIPT_DIR}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release "$@"
cmake --build "${BUILD_DIR}" --parallel "${BUILD_JOBS}"
