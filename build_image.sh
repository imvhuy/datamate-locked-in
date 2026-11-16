#!/usr/bin/env bash
# Build, check and export the submission image (Docker guideline 9-11), in one go.
#
#   TEAM=<lowercase team name> bash build_image.sh
#
# Run from the repo root on Linux or WSL2, with Docker (BuildKit) and, for the GPU test, the NVIDIA Container Toolkit
# and a GPU of 32 GB or more. Needs ~130 GB free disk: ~50 GB image, build cache, ~35 GB archive.
# SKIP_GPU=1 skips the model test (machine without a big GPU); SKIP_EXPORT=1 skips the .tar.gz.
set -euo pipefail
cd "$(dirname "$0")"
TEAM=${TEAM:?set TEAM=<lowercase team name>}
IMAGE=zaic2026_datamate_${TEAM}:final
ARCHIVE=zaic2026_datamate_${TEAM}.tar.gz
REVISION=c202236235762e1c871ad0ccb60c8ee5ba337b9a  # Qwen/Qwen3.5-9B, as declared in README.md
HOSTPY=$(command -v python3 || command -v python)

echo "== 1. inputs the image copies"
if [ ! -f models/model/config.json ]; then
  "$HOSTPY" -m pip install -q "huggingface_hub>=0.30"
  "$HOSTPY" -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3.5-9B', revision='$REVISION', local_dir='models/model')"
fi
[ -f data/train/train/questions.jsonl ] || { echo "the training split must be at data/train/train" >&2; exit 1; }
[ -f training_code/data/bank.jsonl ] || "$HOSTPY" -X utf8 -m training_code.bank

echo "== 2. build"
docker build --platform linux/amd64 -t "$IMAGE" .
docker image inspect -f '{{.Os}}/{{.Architecture}} {{.Size}} bytes' "$IMAGE"
docker run --rm --network none --entrypoint "" "$IMAGE" find /code -maxdepth 2

echo "== 3. CPU check: libraries load, and the example bank replays on the first 60 train questions (no model)"
docker run --rm --network none --entrypoint bash "$IMAGE" -c '
  set -e; cd /code
  /opt/venv/bin/python -c "import vllm, torch; print(\"vllm\", vllm.__version__, \"torch\", torch.__version__, \"cuda\", torch.version.cuda)"
  head -n 60 data/train/train/questions.jsonl > /tmp/q.jsonl
  /opt/venv/bin/python -X utf8 -m src.agent --data data/train/train --questions /tmp/q.jsonl --out /tmp/o --oracle --manifests training_code/manifests > /dev/null
  /opt/venv/bin/python scripts/score.py /tmp/o/submission.jsonl --questions /tmp/q.jsonl | grep "PA ="
  test -s training_code/data_manifest.json && echo "data_manifest.json ok"'

if [ -z "${SKIP_GPU:-}" ]; then
  echo "== 4. GPU test: the scoring command on 20 public questions (LIMIT is for this test only)"
  OUT=$(mktemp -d)
  docker run --rm --gpus all --network none --shm-size=16g --entrypoint bash -e LIMIT=20 \
    -v "$PWD/data/public_test/public_test:/data:ro" -v "$OUT:/result" "$IMAGE" /code/predict.sh
  wc -l "$OUT/submission.jsonl" "$OUT/logs/log.jsonl"
  "$HOSTPY" -c "import json, sys; r = json.loads(open(sys.argv[1], encoding='utf-8').readline()); print({k: r.get(k) for k in ('id', 'answer', 'elapsed_ms')}, 'thinking' in r, 'code' in r)" "$OUT/logs/log.jsonl"
  echo "test output kept in $OUT"
fi

if [ -z "${SKIP_EXPORT:-}" ]; then
  echo "== 5. export, size, checksum"
  docker save "$IMAGE" | { if command -v pigz > /dev/null; then pigz -p 8; else gzip; fi; } > "$ARCHIVE"
  echo "uncompressed .tar bytes: $({ if command -v pigz > /dev/null; then pigz -dc "$ARCHIVE"; else gzip -dc "$ARCHIVE"; fi; } | wc -c) (limit 100 GB)"
  md5sum "$ARCHIVE" > "$ARCHIVE.md5"
  cat "$ARCHIVE.md5"
  ls -la "$ARCHIVE"
fi
