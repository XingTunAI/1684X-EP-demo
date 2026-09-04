#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${1:-bm1684x_v23_09_sp5_neutral_bsp}"
IMAGE_NAME="${SOPHON_BSP_IMAGE:-bm1688_docker:latest}"
SRC="$(pwd)"

sudo docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

sudo docker run -it \
  --name "${CONTAINER_NAME}" \
  --privileged \
  --net=host \
  --shm-size=1g \
  -v "${SRC}:/workspace" \
  -v "${SRC}:${SRC}" \
  -v /dev:/dev \
  -w /workspace \
  "${IMAGE_NAME}" \
  /bin/bash
