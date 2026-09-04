#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VIDEO="${1:-${PROJECT_ROOT}/third_party/sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4}"
DISPLAY_ID="${DISPLAY_ID:-:0}"
XAUTHORITY_PATH="${XAUTHORITY:-}"
PLAYER_LIB_PATH="${PLAYER_LIB_PATH:-/usr/lib/aarch64-linux-gnu}"

if [[ -z "${XAUTHORITY_PATH}" ]]; then
  if [[ -f /var/run/lightdm/root/:0 ]]; then
    XAUTHORITY_PATH="/var/run/lightdm/root/:0"
  else
    XAUTHORITY_PATH="${HOME}/.Xauthority"
  fi
fi

if [[ ! -f "${VIDEO}" ]]; then
  echo "Video not found: ${VIDEO}" >&2
  exit 1
fi

export DISPLAY="${DISPLAY_ID}"
export XAUTHORITY="${XAUTHORITY_PATH}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}"

echo "Playing YOLO result on HDMI/XFCE display ${DISPLAY}"
echo "  video: ${VIDEO}"
echo "  xauth: ${XAUTHORITY}"

if command -v ffplay >/dev/null 2>&1; then
  exec env LD_LIBRARY_PATH="${PLAYER_LIB_PATH}" ffplay -fs -autoexit "${VIDEO}"
fi

if command -v mpv >/dev/null 2>&1; then
  exec env LD_LIBRARY_PATH="${PLAYER_LIB_PATH}" mpv --fs "${VIDEO}"
fi

if command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "${VIDEO}"
fi

echo "No player found. Install mpv or use ffplay from ffmpeg." >&2
exit 1
