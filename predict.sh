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

GPUS=$( (nvidia-smi -L 2>/dev/null || true) | grep -c '^GPU' || true)
if [ "${GPUS:-0}" -lt 1 ]; then
  GPUS=$("$PY" -c "import torch; print(torch.cuda.device_count())")
fi
echo "GPUs: $GPUS"

PIDS=()
for i in $(seq 0 $((GPUS - 1))); do
  CUDA_VISIBLE_DEVICES=$i "$PY" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" --served-model-name model --port $((8000 + i)) --host 127.0.0.1 \
    --max-model-len 32768 --seed 0 --gpu-memory-utilization 0.90 --reasoning-parser qwen3 --language-model-only \
    > "$RESULT/logs/vllm_$i.log" 2>&1 &
  PIDS+=($!)
done

up() {  # the server on port $1 answers
  "$PY" -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=3)" "http://127.0.0.1:$1/v1/models" 2> /dev/null
}

URLS=""
for i in $(seq 0 $((GPUS - 1))); do  # model loading: up to 20 minutes per server
  for _ in $(seq 240); do
    up $((8000 + i)) && break
    kill -0 "${PIDS[$i]}" 2> /dev/null || break  # the server exited: see its log
    sleep 5
  done
  # A server that never came up is left out; the others take its questions.
  if up $((8000 + i)); then
    URLS="${URLS:+$URLS,}http://127.0.0.1:$((8000 + i))/v1"
  else
    echo "GPU $i: no model server, see $RESULT/logs/vllm_$i.log" >&2
  fi
done
if [ -z "$URLS" ]; then
  echo "no model server started; see $RESULT/logs/vllm_*.log" >&2
  exit 1
fi

# LIMIT: first N questions only, for a smoke test. The organizers run without it.
"$PY" -X utf8 -m src.agent --data "$DATA" --out "$RESULT" --llm "$URLS" --workers $((GPUS * 8)) ${LIMIT:+--limit "$LIMIT"}
