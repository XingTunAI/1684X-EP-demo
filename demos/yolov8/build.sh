#!/usr/bin/env bash
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DEMO_DIR}/../.." && pwd)"
BUILD_DIR="${BUILD_DIR:-${DEMO_DIR}/build}"
BUILD_JOBS="${BUILD_JOBS:-2}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: bash demos/yolov8/build.sh [CMake configuration arguments...]

Builds demos/yolov8/build/pipeline_worker.pcie by default.
Requires CMake 3.13+, a C++11 compiler and the Sophon SDK dependencies.

Environment:
  BUILD_DIR   Build directory; relative paths are based on the repository root.
  BUILD_JOBS  Parallel build jobs (default: 2).

Examples:
  bash demos/yolov8/build.sh
  bash demos/yolov8/build.sh -DSOPHON_DEMO_DIR=/path/to/sophon-demo
  BUILD_DIR=demos/yolov8/build-debug bash demos/yolov8/build.sh -DCMAKE_BUILD_TYPE=Debug

Other arguments are passed to CMake unchanged. Use BUILD_DIR to change the
output location. This script does not install SDK packages or run a device.
USAGE
  exit 0
fi

cd "${PROJECT_ROOT}"
cmake -S "${DEMO_DIR}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release "$@"
cmake --build "${BUILD_DIR}" --parallel "${BUILD_JOBS}"
