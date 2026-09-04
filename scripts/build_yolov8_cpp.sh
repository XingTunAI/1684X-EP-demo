#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOPHON_DEMO_DIR="${1:-${SOPHON_DEMO_DIR:-${PROJECT_ROOT}/third_party/sophon-demo}}"
YOLO_CPP_DIR="${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
YOLO_MAIN_SRC="${PROJECT_ROOT}/src/yolov8_bmcv/main.cpp"

if [[ ! -d "${YOLO_CPP_DIR}" ]]; then
  echo "Cannot find YOLOv8 C++ directory: ${YOLO_CPP_DIR}" >&2
  echo "Run scripts/prepare_yolov8_demo.sh first." >&2
  exit 2
fi

if [[ ! -f "${YOLO_MAIN_SRC}" ]]; then
  echo "Cannot find repository YOLOv8 C++ source: ${YOLO_MAIN_SRC}" >&2
  exit 2
fi

echo "[1/4] Checking SOPHON install paths"
test -d /opt/sophon/sophon-ffmpeg-latest/lib/cmake
test -d /opt/sophon/sophon-opencv-latest/lib/cmake/opencv4
test -d /opt/sophon/libsophon-current/lib

missing_headers=()
for header in bmruntime_interface.h bmcv_api_ext.h bmlib_runtime.h; do
  if ! find -L /opt/sophon/libsophon-current/include /usr/include /usr/local/include \
      -name "${header}" -print -quit 2>/dev/null | grep -q .; then
    missing_headers+=("${header}")
  fi
done

if [[ "${#missing_headers[@]}" -gt 0 ]]; then
  echo "Missing libsophon development headers:" >&2
  printf '  %s\n' "${missing_headers[@]}" >&2
  echo >&2
  echo "Install sophon-libsophon-dev, or copy the include directory from the matching SDK package." >&2
  echo "Expected SDK source example:" >&2
  echo "  libsophon_0.5.1_aarch64/opt/sophon/libsophon-0.5.1/include" >&2
  exit 2
fi

echo "[2/3] Syncing repository YOLOv8 C++ source"
cp "${YOLO_MAIN_SRC}" "${YOLO_CPP_DIR}/main.cpp"

echo "[3/4] Building YOLOv8 C++ PCIe demo"
pushd "${YOLO_CPP_DIR}" >/dev/null
rm -rf build
mkdir build
pushd build >/dev/null
cmake ..
make -j"$(nproc)"
popd >/dev/null
popd >/dev/null

echo "[4/4] Checking executable"
test -x "${YOLO_CPP_DIR}/yolov8_bmcv.pcie"

echo
echo "Build finished:"
echo "  ${YOLO_CPP_DIR}/yolov8_bmcv.pcie"
