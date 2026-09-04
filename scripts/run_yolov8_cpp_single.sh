#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-${PROJECT_ROOT}/third_party/sophon-demo}"
DEV_ID="${1:-1}"
INPUT="${2:-../../datasets/test_car_person_1080P.mp4}"
BMODEL="${3:-../../models/BM1684X/yolov8s_int8_1b.bmodel}"
CLASSNAMES="${4:-../../datasets/coco.names}"

YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
EXE="${YOLO_CPP_DIR}/yolov8_bmcv.pcie"

if [[ ! -x "${EXE}" ]]; then
  echo "Cannot find executable: ${EXE}" >&2
  echo "Run scripts/build_yolov8_cpp.sh first." >&2
  exit 2
fi

echo "Running YOLOv8 C++ demo on dev_id=${DEV_ID}"
echo "Board mapping: dev_id=1 is Gen3 primary, dev_id=0 is Gen2 secondary."
pushd "${YOLO_CPP_DIR}" >/dev/null
./yolov8_bmcv.pcie \
  --input="${INPUT}" \
  --bmodel="${BMODEL}" \
  --dev_id="${DEV_ID}" \
  --conf_thresh=0.25 \
  --nms_thresh=0.7 \
  --classnames="${CLASSNAMES}"
popd >/dev/null

echo
echo "Result video should be under:"
echo "  ${YOLO_CPP_DIR}/results/output.mp4"
