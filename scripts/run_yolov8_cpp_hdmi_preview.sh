#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-${PROJECT_ROOT}/third_party/sophon-demo}"
DEV_ID="${1:-1}"
INPUT="${2:-../../datasets/test_car_person_1080P.mp4}"
BMODEL="${3:-../../models/BM1684X/yolov8s_int8_1b.bmodel}"
CLASSNAMES="${4:-../../datasets/coco.names}"
DISPLAY_ID="${DISPLAY_ID:-:0}"
XAUTHORITY_PATH="${XAUTHORITY:-}"
DISPLAY_SIZE="${DISPLAY_SIZE:-960x540}"
DISPLAY_WIDTH="${DISPLAY_WIDTH:-${DISPLAY_SIZE%x*}}"
SAVE_VIDEO="${SAVE_VIDEO:-0}"
PLAYER_LIB_PATH="${PLAYER_LIB_PATH:-/usr/lib/aarch64-linux-gnu}"
FIFO_PATH="${FIFO_PATH:-/tmp/yolov8_dev${DEV_ID}.bgr}"

if [[ -z "${XAUTHORITY_PATH}" ]]; then
  if [[ -f /var/run/lightdm/root/:0 ]]; then
    XAUTHORITY_PATH="/var/run/lightdm/root/:0"
  else
    XAUTHORITY_PATH="${HOME}/.Xauthority"
  fi
fi

export DISPLAY="${DISPLAY_ID}"
export XAUTHORITY="${XAUTHORITY_PATH}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}"

YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
EXE="${YOLO_CPP_DIR}/yolov8_bmcv.pcie"

if [[ ! -x "${EXE}" ]]; then
  echo "Cannot find executable: ${EXE}" >&2
  echo "Run scripts/build_yolov8_cpp.sh first." >&2
  exit 2
fi

if ! "${EXE}" --help 2>&1 | grep -q -- "--display_fifo"; then
  echo "The YOLOv8 C++ executable does not include HDMI FIFO display support." >&2
  echo "Apply patches/yolov8_bmcv_hdmi_display.patch and patches/yolov8_bmcv_hdmi_fifo.patch, then rebuild." >&2
  exit 2
fi

echo "Running YOLOv8 HDMI preview on dev_id=${DEV_ID}"
echo "  display: ${DISPLAY}"
echo "  xauth:   ${XAUTHORITY}"
echo "  size:    ${DISPLAY_SIZE}"
echo "  fifo:    ${FIFO_PATH}"
echo "  save:    ${SAVE_VIDEO}"

rm -f "${FIFO_PATH}"
mkfifo "${FIFO_PATH}"
PLAYER_PID=""
cleanup() {
  if [[ -n "${PLAYER_PID}" ]]; then
    kill "${PLAYER_PID}" 2>/dev/null || true
  fi
  rm -f "${FIFO_PATH}"
}
trap cleanup EXIT

env LD_LIBRARY_PATH="${PLAYER_LIB_PATH}" ffplay \
  -hide_banner \
  -loglevel warning \
  -f rawvideo \
  -pixel_format bgr24 \
  -video_size "${DISPLAY_SIZE}" \
  -framerate 25 \
  -i "${FIFO_PATH}" &
PLAYER_PID="$!"

pushd "${YOLO_CPP_DIR}" >/dev/null
./yolov8_bmcv.pcie \
  --input="${INPUT}" \
  --bmodel="${BMODEL}" \
  --dev_id="${DEV_ID}" \
  --conf_thresh=0.25 \
  --nms_thresh=0.7 \
  --classnames="${CLASSNAMES}" \
  --display=0 \
  --display_wait=1 \
  --display_width="${DISPLAY_WIDTH}" \
  --display_fifo="${FIFO_PATH}" \
  --save_video="${SAVE_VIDEO}"
popd >/dev/null

wait "${PLAYER_PID}" || true
