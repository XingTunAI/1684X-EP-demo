#!/usr/bin/env bash
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${BUILD_DIR:-${TOOL_DIR}/build}"
BUILD_JOBS="${BUILD_JOBS:-2}"

cmake -S "${TOOL_DIR}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release "$@"
cmake --build "${BUILD_DIR}" --parallel "${BUILD_JOBS}"
