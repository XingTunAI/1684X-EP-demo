#!/usr/bin/env bash
set -euo pipefail

DEB_DIR="${1:-sophon-debs-0.5.1_LTS}"

if [[ ! -d "${DEB_DIR}" ]]; then
  echo "Cannot find deb directory: ${DEB_DIR}" >&2
  exit 2
fi

echo "[1/5] Board information"
uname -a
dpkg --print-architecture
cat /etc/os-release || true

echo "[2/5] Installing SOPHON driver and runtime"
sudo apt install -y \
  "${DEB_DIR}/sophon-driver_0.5.1-LTS-rk3588fix2_arm64.deb" \
  "${DEB_DIR}/sophon-libsophon_0.5.1-LTS_arm64.deb"

echo "[3/5] Installing SOPHON multimedia packages"
sudo apt install -y \
  "${DEB_DIR}/sophon-mw-sophon-ffmpeg_0.14.0_arm64.deb" \
  "${DEB_DIR}/sophon-mw-sophon-ffmpeg-dev_0.14.0_arm64.deb" \
  "${DEB_DIR}/sophon-mw-sophon-opencv_0.14.0_arm64.deb" \
  "${DEB_DIR}/sophon-mw-sophon-opencv-dev_0.14.0_arm64.deb"

echo "[4/5] Fixing dependencies if needed"
sudo apt --fix-broken install -y

echo "[5/5] Basic verification"
lspci | grep -i -E "1f1c|processing" || true
lsmod | grep -i -E "bm|sophon" || true
command -v bm-smi && bm-smi || true
ldconfig -p | grep -E "bmrt|bmlib|bmcv" || true

echo
echo "Install script finished. Reboot the board before running demos:"
echo "  sudo reboot"
