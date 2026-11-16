# DataMate (Zalo AI Challenge 2026), SLM track

Answers Vietnamese questions about the sales data of small shops (CSV / XLSX / SQLite). Entry point:
`bash /code/predict.sh` reads `/data` and writes `/result/submission.jsonl` and one log record per question in
`/result/logs/log.jsonl`.

## Approach

The model never computes a number itself: every answer is the value of a program run on the shop's data.

1. **Data layer, rules only** (`src/load.py`, `src/schema.py`, `src/shop.py`). Reads every file of a shop;
   parses mixed date and money formats; drops report titles, totals and duplicate export rows; fills the blank
   cells of an order's later rows. It maps columns to roles (order, date, product, quantity, customer...) from the
   shop README and a column-name lexicon learned on the train shops. When the rules cannot place a needed column
   (new line of business, silent README), the model completes the mapping (`src/mapper.py`).
2. **Semantic layer** (`src/period.py`, `src/metrics.py`). Methods written from the rulebook: valid orders,
   revenue, base units, periods relative to the question's date (lunar Tết from `calendar.csv`), stock, debts,
   rankings, line-of-business books (instalments, warranty, repair tickets, multi-trip deliveries...).
3. **Program writing** (`src/agent.py`). For each question the model (thinking mode) gets:
   - the shop description;
   - the API;
   - the rulebook of the split, with the rows nearest the question quoted;
   - a few solved train questions from other shops (`training_code/data/bank.jsonl`).

   It replies with an answer type and a short Python program over the methods, or `no_data(reason)` when the
   shop does not record what is asked. If the reasoning reaches the token limit, it is closed and the model writes
   its reply from it (budget forcing).
4. **Verification** (`src/verify.py`, `src/asked.py`, `check()` in `src/agent.py`). Before and after running,
   the program is checked against the question:
   - periods and months named;
   - products that exist;
   - impossible results (a zero from a filter that matched nothing, a share above 100%...);
   - rulebook rows the program ignores.

   A problem goes back to the model once, with the reason. If the retry is still wrong, the question is left
   unanswered (`abstain`, 0 points) rather than risk −1. Every rule is kept only if it rejects no train gold
   answer (`python -X utf8 -m training_code.verify_fp`).
5. **Answer** (`run_program`). Converts the result to the required format (rounding of the rulebook, dates,
   tables).

No answer, rule or branch depends on a question id or business id.

## Model

| | |
|---|---|
| Name | Qwen/Qwen3.5-9B (chat model, used as released; no fine-tuning) |
| Source | https://huggingface.co/Qwen/Qwen3.5-9B |
| Revision | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| Released | 2026-02-27 |
| License | Apache-2.0 |
| Parameters | 9.65B (total of all inference models; no embedding, reranking or other model) |
| In the image | `/code/models/model` (bf16 safetensors, tokenizer, chat template) |

Served by vLLM 0.17.1 (in `/opt/venv`), one server per GPU (`--max-model-len 32768`, `--language-model-only`,
`--reasoning-parser qwen3`). Decoding settings are fixed in the code:
- **Thinking replies:** temperature 0.6, top_p 0.95, top_k 20 (the model card's setting: greedy decoding degrades
  thinking), at most 8192 tokens.
- **Non-thinking calls:** temperature 0.7, top_p 0.8.
- **Seed:** every request carries one derived from the question id, so a re-run draws the same samples.

## Data

- Provided training split only (`/code/data/train/train`: 30 shops, 3000 questions with answers). The public and
  private test sets are never used for training, examples or lexicon.
- Generated from it, with no manual labels:
  - the example bank (2928 verified question → program pairs);
  - the column lexicon (`src/lexicon.json`);
  - measurement splits.

  See `training_code/README.md`. Paths, sizes and SHA-256 of all data and weights:
  `training_code/data_manifest.json`, written when the image is built.

## Run

```bash
docker run --rm --gpus '"device=0,1"' --network none --shm-size=16g --entrypoint bash \
  -v /path/to/test:/data:ro -v /path/to/output:/result zaic2026_datamate_<team>:final /code/predict.sh
```

Model loading takes a few minutes. Answers are written as they are found, and every server log goes to
`/result/logs/vllm_<gpu>.log`.

## Layout

```
/code/predict.sh            entry point
/code/src/                  inference code (data layer, metrics, agent, verification)
/code/scripts/              scoring (score.py) and submission checks
/code/models/model/         Qwen3.5-9B weights
/code/data/train/train/     provided training split
/code/training_code/        data tooling, example bank, splits, data_manifest.json
/opt/venv/                  Python 3.12 + vLLM 0.17.1 (requirements.txt)
```

## Build

```bash
TEAM=<team> bash build_image.sh
```

`build_image.sh` does every step of the guideline (sections 9-11):
1. downloads the weights at the declared revision into `models/model` if they are missing;
2. builds the image;
3. checks it without a GPU (libraries load, the example bank replays on 60 train questions);
4. runs the scoring command on 20 public questions (`LIMIT=20`, a test-only setting; `predict.sh` runs every
   question without it);
5. exports `zaic2026_datamate_<team>.tar.gz` with its `.md5` and the uncompressed size.

The training split must be at `data/train/train` before the build.

Without a Docker host, `notebooks/colab_image.ipynb` builds the same image on a Colab A100:
1. it lays out `/code`, `/opt/venv` and `/opt/python` exactly as in the image;
2. it runs `predict.sh` itself on 30 public questions on the GPU;
3. it appends those folders as one layer to `vllm/vllm-openai:v0.10.1.1` with `crane`, which needs no Docker daemon.
   It sets ENTRYPOINT `bash` and CMD `/code/predict.sh`: a plain `docker run` and the scoring command both run
   `bash /code/predict.sh`;
4. it writes the `.tar.gz` and its `.md5` to Google Drive.
