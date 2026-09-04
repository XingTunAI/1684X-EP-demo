#!/usr/bin/env bash
set -euo pipefail

SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-sophon-demo}"
YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
EXE="${YOLO_CPP_DIR}/yolov8_bmcv.pcie"

if [[ ! -x "${EXE}" ]]; then
  echo "Cannot find executable: ${EXE}" >&2
  echo "Run scripts/build_yolov8_cpp.sh first." >&2
  exit 2
fi

run_one() {
  local dev_id="$1"
  local log_file="yolov8_dev${dev_id}.log"
  (
    cd "${YOLO_CPP_DIR}"
    ./yolov8_bmcv.pcie \
      --input=../../datasets/test_car_person_1080P.mp4 \
      --bmodel=../../models/BM1684X/yolov8s_int8_1b.bmodel \
      --dev_id="${dev_id}" \
      --conf_thresh=0.25 \
      --nms_thresh=0.7 \
      --classnames=../../datasets/coco.names
  ) >"${log_file}" 2>&1 &
  echo $!
}

echo "Starting YOLOv8 on dev_id=0 and dev_id=1"
PID0="$(run_one 0)"
PID1="$(run_one 1)"

echo "Process dev0: ${PID0}"
echo "Process dev1: ${PID1}"
echo "Logs:"
echo "  yolov8_dev0.log"
echo "  yolov8_dev1.log"
echo
echo "Use another terminal to watch devices:"
echo "  watch -n 1 bm-smi"

wait "${PID0}"
wait "${PID1}"
