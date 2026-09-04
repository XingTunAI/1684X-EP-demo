#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-${PROJECT_ROOT}/third_party/sophon-demo}"
YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
PATCH_FILE="${PATCH_FILE:-patches/yolov8_bmcv_card_writer_dev_id.patch}"

if [[ ! -d "${YOLO_CPP_DIR}" ]]; then
  echo "Cannot find YOLOv8 C++ directory: ${YOLO_CPP_DIR}" >&2
  echo "Run scripts/prepare_yolov8_demo.sh first, or set SOPHON_DEMO_DIR." >&2
  exit 2
fi

if [[ ! -f "${PATCH_FILE}" ]]; then
  echo "Cannot find patch file: ${PATCH_FILE}" >&2
  echo "Run this script from the repository root, or set PATCH_FILE." >&2
  exit 2
fi

pushd "${YOLO_CPP_DIR}" >/dev/null
if grep -q 'writer.open(output_path, output_fourcc, frameRate, cv::Size(w, h), true, dev_id);' main.cpp; then
  echo "Card writer dev_id patch is already applied."
else
  patch -p1 <"${OLDPWD}/${PATCH_FILE}"
fi
popd >/dev/null

echo "Card writer dev_id patch applied to:"
echo "  ${YOLO_CPP_DIR}/main.cpp"
echo
echo "Rebuild with:"
echo "  SOPHON_DEMO_DIR=${SOPHON_DEMO_DIR} bash scripts/build_yolov8_cpp.sh"
