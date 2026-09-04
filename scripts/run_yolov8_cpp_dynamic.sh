#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-${PROJECT_ROOT}/third_party/sophon-demo}"
YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
EXE="${YOLO_CPP_DIR}/yolov8_bmcv.pcie"
BMODEL="${BMODEL:-${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel}"
CLASSNAMES="${CLASSNAMES:-${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/datasets/coco.names}"
CONF_THRESH="${CONF_THRESH:-0.25}"
NMS_THRESH="${NMS_THRESH:-0.7}"
RUN_ROOT="${RUN_ROOT:-/tmp/1684x_ep_demo/yolov8_runs}"

# Format: dev_id|weight|role|pcie_bdf|pcie_link
# dev_id 1 is the confirmed Gen3 card, so it receives twice the default weight.
DEVICE_SPECS="${DEVICE_SPECS:-1|2|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1}"

if [[ ! -x "${EXE}" ]]; then
  echo "Cannot find executable: ${EXE}" >&2
  echo "Run scripts/build_yolov8_cpp.sh first." >&2
  exit 2
fi

IFS=',' read -r -a specs <<<"${DEVICE_SPECS}"
if [[ "${#specs[@]}" -eq 0 ]]; then
  echo "No devices configured." >&2
  exit 2
fi

expanded_devices=()
for spec in "${specs[@]}"; do
  IFS='|' read -r dev_id weight role bdf link <<<"${spec}"
  if [[ -z "${dev_id:-}" || -z "${weight:-}" ]]; then
    echo "Invalid device spec: ${spec}" >&2
    exit 2
  fi
  for ((i = 0; i < weight; i++)); do
    expanded_devices+=("${dev_id}|${role:-worker}|${bdf:-unknown}|${link:-unknown}")
  done
done

if [[ "$#" -gt 0 ]]; then
  inputs=("$@")
else
  INPUTS="${INPUTS:-${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4}"
  IFS=',' read -r -a inputs <<<"${INPUTS}"
fi
if [[ "${#inputs[@]}" -eq 0 ]]; then
  echo "No inputs configured." >&2
  exit 2
fi

mkdir -p "${RUN_ROOT}"
run_id="$(date +%Y%m%d_%H%M%S)"
log_dir="${RUN_ROOT}/${run_id}/logs"
mkdir -p "${log_dir}"

echo "Dynamic YOLOv8 task assignment"
echo "  source: ${YOLO_CPP_DIR}"
echo "  run:    ${RUN_ROOT}/${run_id}"
echo "  model:  ${BMODEL}"
echo

pids=()
for index in "${!inputs[@]}"; do
  input="${inputs[$index]}"
  device="${expanded_devices[$((index % ${#expanded_devices[@]}))]}"
  IFS='|' read -r dev_id role bdf link <<<"${device}"

  work_dir="${RUN_ROOT}/${run_id}/task${index}_dev${dev_id}"
  log_file="${log_dir}/task${index}_dev${dev_id}.log"
  rm -rf "${work_dir}"
  cp -a "${YOLO_CPP_DIR}" "${work_dir}"

  echo "  task${index}: dev_id=${dev_id}, role=${role}, bdf=${bdf}, link=${link}, input=${input}"
  (
    cd "${work_dir}"
    ./yolov8_bmcv.pcie \
      --input="${input}" \
      --bmodel="${BMODEL}" \
      --dev_id="${dev_id}" \
      --conf_thresh="${CONF_THRESH}" \
      --nms_thresh="${NMS_THRESH}" \
      --classnames="${CLASSNAMES}"
  ) >"${log_file}" 2>&1 &
  pids+=("$!")
done

echo
echo "Logs:"
printf '  %s\n' "${log_dir}"/*.log
echo
echo "Use another terminal to watch devices:"
echo "  /opt/sophon/libsophon-current/bin/bm-smi"
echo

exit_code=0
for pid in "${pids[@]}"; do
  if ! wait "${pid}"; then
    exit_code=1
  fi
done

echo
echo "Result videos:"
find "${RUN_ROOT}/${run_id}" -path '*/results/output.mp4' -print

exit "${exit_code}"
