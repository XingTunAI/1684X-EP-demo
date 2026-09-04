#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-${PROJECT_ROOT}/third_party/sophon-demo}"
TEST_VIDEO="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4"

# Two demo streams are assigned by weight:
#   task0 -> dev_id 1, Gen3 primary
#   task1 -> dev_id 1 again if a third stream is added, otherwise dev_id 0 for this second stream
# For an explicit two-card smoke test, keep one input per card below.
DEVICE_SPECS="${DEVICE_SPECS:-1|1|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1}" \
INPUTS="${INPUTS:-${TEST_VIDEO},${TEST_VIDEO}}" \
bash "${SCRIPT_DIR}/run_yolov8_cpp_dynamic.sh"
