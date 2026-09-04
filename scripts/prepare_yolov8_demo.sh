#!/usr/bin/env bash
set -euo pipefail

SOPHON_DEMO_DIR="${1:-sophon-demo}"
SOPHON_DEMO_REPO="${SOPHON_DEMO_REPO:-https://github.com/sophgo/sophon-demo.git}"
SOPHON_DEMO_BRANCH="${SOPHON_DEMO_BRANCH:-release}"

echo "[1/4] Installing basic build and download tools"
sudo apt update
sudo apt install -y git cmake make g++ pkg-config python3 python3-pip

echo "[2/4] Preparing official sophon-demo repository"
if [[ ! -d "${SOPHON_DEMO_DIR}/.git" ]]; then
  git clone --depth 1 -b "${SOPHON_DEMO_BRANCH}" "${SOPHON_DEMO_REPO}" "${SOPHON_DEMO_DIR}"
else
  echo "${SOPHON_DEMO_DIR} already exists, skip clone"
fi

YOLO_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det"
if [[ ! -d "${YOLO_DIR}" ]]; then
  echo "Cannot find YOLOv8 demo directory: ${YOLO_DIR}" >&2
  exit 2
fi

echo "[3/4] Downloading YOLOv8 BM1684X model and test data"
pushd "${YOLO_DIR}" >/dev/null
chmod -R +x scripts/
./scripts/download.sh --BM1684X
popd >/dev/null

echo "[4/4] Checking expected files"
test -f "${YOLO_DIR}/datasets/test_car_person_1080P.mp4"
test -f "${YOLO_DIR}/datasets/coco.names"
test -f "${YOLO_DIR}/models/BM1684X/yolov8s_int8_1b.bmodel"

echo
echo "YOLOv8 demo assets are ready:"
echo "  ${YOLO_DIR}/datasets/test_car_person_1080P.mp4"
echo "  ${YOLO_DIR}/datasets/coco.names"
echo "  ${YOLO_DIR}/models/BM1684X/yolov8s_int8_1b.bmodel"
