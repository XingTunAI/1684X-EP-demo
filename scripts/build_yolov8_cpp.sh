#!/usr/bin/env bash
set -euo pipefail

SOPHON_DEMO_DIR="${1:-sophon-demo}"
YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"

if [[ ! -d "${YOLO_CPP_DIR}" ]]; then
  echo "Cannot find YOLOv8 C++ directory: ${YOLO_CPP_DIR}" >&2
  echo "Run scripts/prepare_yolov8_demo.sh first." >&2
  exit 2
fi

echo "[1/3] Checking SOPHON install paths"
test -d /opt/sophon/sophon-ffmpeg-latest/lib/cmake
test -d /opt/sophon/sophon-opencv-latest/lib/cmake/opencv4

echo "[2/3] Building YOLOv8 C++ PCIe demo"
pushd "${YOLO_CPP_DIR}" >/dev/null
rm -rf build
mkdir build
pushd build >/dev/null
cmake ..
make -j"$(nproc)"
popd >/dev/null
popd >/dev/null

echo "[3/3] Checking executable"
test -x "${YOLO_CPP_DIR}/yolov8_bmcv.pcie"

echo
echo "Build finished:"
echo "  ${YOLO_CPP_DIR}/yolov8_bmcv.pcie"
