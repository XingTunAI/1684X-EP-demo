#!/usr/bin/env bash
# Fetch the official BM1684X nano archive without replacing existing models.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X"
NAMES=(yolov8n_int8_1b.bmodel yolov8n_int8_4b.bmodel)
HASHES=(a9d6cec7390072402f6ea5bedef18c9262d9566983a713268b16908a3a63be32 7216f90fa30d209fe1bdf8fd6fc013547653999df6b057f90af42b169d96b65d)
complete=1
for i in 0 1; do
    if [[ -f "$DEST/${NAMES[$i]}" ]]; then
        echo "${HASHES[$i]}  $DEST/${NAMES[$i]}" | sha256sum --check --status || {
            echo "Existing model has a different hash: ${NAMES[$i]}" >&2; exit 1;
        }
    else
        complete=0
    fi
done
if [[ "$complete" == 1 ]]; then
    echo "YOLOv8n models already present and hashes verified."
    exit 0
fi
python3 -c 'import dfss' || { echo 'Install dfss for the current user before downloading.' >&2; exit 1; }
mkdir -p "$ROOT/results/model-candidates" "$DEST"
WORK="$(mktemp -d "$ROOT/results/model-candidates/yolov8n.XXXXXX")"
cd "$WORK"
python3 -m dfss --url=open@sophgo.com:sophon-demo/YOLOv8_plus_det/models_yolov8_nano/BM1684X.tar.gz
echo 'b5920431715e47b12c60f238188b50e494089173e008f25d26c00fe2a2abd414  BM1684X.tar.gz' | sha256sum --check
tar xzf BM1684X.tar.gz BM1684X/yolov8n_int8_1b.bmodel BM1684X/yolov8n_int8_4b.bmodel
for i in 0 1; do
    echo "${HASHES[$i]}  BM1684X/${NAMES[$i]}" | sha256sum --check
    if [[ ! -e "$DEST/${NAMES[$i]}" ]]; then
        cp -n "BM1684X/${NAMES[$i]}" "$DEST/${NAMES[$i]}"
    fi
    echo "${HASHES[$i]}  $DEST/${NAMES[$i]}" | sha256sum --check
done
echo "Models prepared. Archive retained in $WORK"
