#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-${PROJECT_ROOT}/third_party/sophon-demo}"
YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
DISPLAY_PATCH_FILE="${DISPLAY_PATCH_FILE:-patches/yolov8_bmcv_hdmi_display.patch}"
FIFO_PATCH_FILE="${FIFO_PATCH_FILE:-patches/yolov8_bmcv_hdmi_fifo.patch}"

if [[ ! -d "${YOLO_CPP_DIR}" ]]; then
  echo "Cannot find YOLOv8 C++ directory: ${YOLO_CPP_DIR}" >&2
  echo "Set SOPHON_DEMO_DIR to the official sophon-demo directory." >&2
  exit 2
fi

for patch_file in "${DISPLAY_PATCH_FILE}" "${FIFO_PATCH_FILE}"; do
  if [[ ! -f "${patch_file}" ]]; then
    echo "Cannot find patch file: ${patch_file}" >&2
    echo "Run this script from the repository root, or set DISPLAY_PATCH_FILE/FIFO_PATCH_FILE." >&2
    exit 2
  fi
done

pushd "${YOLO_CPP_DIR}" >/dev/null
if grep -q '"{display | 0 | display result frames with OpenCV HighGUI.}"' main.cpp; then
  echo "HDMI display patch is already applied."
else
  patch -p1 <"${OLDPWD}/${DISPLAY_PATCH_FILE}"
fi
if grep -q '"{display_fifo | | write rendered BGR24 frames to this FIFO path.}"' main.cpp; then
  echo "HDMI FIFO patch is already applied."
else
  patch -p1 <"${OLDPWD}/${FIFO_PATCH_FILE}"
fi
popd >/dev/null

echo "HDMI display patch applied to:"
echo "  ${YOLO_CPP_DIR}/main.cpp"
echo
echo "Rebuild with:"
echo "  SOPHON_DEMO_DIR=${SOPHON_DEMO_DIR} bash scripts/build_yolov8_cpp.sh"
