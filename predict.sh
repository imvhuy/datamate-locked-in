#!/usr/bin/env bash
# Entry point (Docker guideline 5): reads /data, writes /result/submission.jsonl and /result/logs/. No arguments.
# Qwen3.5-9B in bf16 (~19 GB) fits one 32 GB RTX 5090, so every GPU runs its own vLLM server (no tensor parallel,
# no NCCL); the agent spreads questions over them by question id.
set -euo pipefail
cd "$(dirname "$0")"

DATA=${DATA:-/data}
RESULT=${RESULT:-/result}
MODEL=${MODEL:-/code/models/model}
PY=${PY:-/opt/venv/bin/python}
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONIOENCODING=utf-8
mkdir -p "$RESULT/logs"

GPUS=$(nvidia-smi -L | wc -l)
for i in $(seq 0 $((GPUS - 1))); do
  CUDA_VISIBLE_DEVICES=$i "$PY" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" --served-model-name model --port $((8000 + i)) --host 127.0.0.1 \
    --max-model-len 32768 --seed 0 --gpu-memory-utilization 0.90 --reasoning-parser qwen3 --language-model-only \
    > "$RESULT/logs/vllm_$i.log" 2>&1 &
done

URLS=""
for i in $(seq 0 $((GPUS - 1))); do  # model loading: up to 20 minutes per server
  for _ in $(seq 240); do
    curl -sf "http://127.0.0.1:$((8000 + i))/v1/models" > /dev/null && break
    sleep 5
  done
  # A server that never came up is left out; the others take its questions.
  curl -sf "http://127.0.0.1:$((8000 + i))/v1/models" > /dev/null && URLS="${URLS:+$URLS,}http://127.0.0.1:$((8000 + i))/v1"
done
if [ -z "$URLS" ]; then
  echo "no model server started; see $RESULT/logs/vllm_*.log" >&2
  exit 1
fi

"$PY" -X utf8 -m src.agent --data "$DATA" --out "$RESULT" --llm "$URLS" --workers $((GPUS * 8))
