#!/usr/bin/env bash
# Entry point (Docker guideline 5): reads /data, writes /result/submission.jsonl and /result/logs/. No arguments.
# One vLLM server per GPU (no tensor parallel, so no NCCL between GPUs); the agent spreads questions over them.
set -euo pipefail
cd "$(dirname "$0")"

DATA=${DATA:-/data}
RESULT=${RESULT:-/result}
MODEL=${MODEL:-/code/models/model}
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONIOENCODING=utf-8
mkdir -p "$RESULT/logs"

GPUS=$(nvidia-smi -L | wc -l)
URLS=""
for i in $(seq 0 $((GPUS - 1))); do
  port=$((8000 + i))
  CUDA_VISIBLE_DEVICES=$i python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" --served-model-name model --port "$port" --host 127.0.0.1 \
    --max-model-len 32768 --seed 0 --gpu-memory-utilization 0.92 --reasoning-parser qwen3 \
    > "$RESULT/logs/vllm_$i.log" 2>&1 &
  URLS="${URLS:+$URLS,}http://127.0.0.1:$port/v1"
done

for i in $(seq 0 $((GPUS - 1))); do  # wait for every server: up to 20 minutes each
  for _ in $(seq 240); do
    curl -sf "http://127.0.0.1:$((8000 + i))/v1/models" > /dev/null && break
    sleep 5
  done
done

python -X utf8 -m src.agent --data "$DATA" --out "$RESULT" --llm "$URLS" --workers $((GPUS * 8))
