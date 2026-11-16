# training_code

**No model was trained or fine-tuned.** The solution runs Qwen/Qwen3.5-9B as released (see `/code/README.md`).

This folder holds the data tooling: everything generated from the provided training split and used at inference,
with the code that regenerates it.

## What is generated, from what

| Output | Built by | From | Used at inference |
|---|---|---|---|
| `data/bank.jsonl` (2928 rows) | `bank.py` + `templates.py` | `data/train/train` (questions, answers, shop files) | yes: few-shot examples the agent retrieves |
| `data/missed.jsonl` | `bank.py` | same | no: train questions no template solved (development log) |
| `/code/src/lexicon.json` | `lexicon.py` | READMEs and columns of the train shops | yes: column-name lexicon for shops whose README is silent |
| `splits/dev300.jsonl`, `splits/holdout300.jsonl` | first / last 300 lines of train `questions.jsonl` | same | no: measurement only |
| `splits/hidden_rules.jsonl` | `hidden_split.py` | same | no: measures questions whose rule no method implements |
| `manifests/*.json` | hand-written | 4 train shops | no: development only, `predict.sh` never passes `--manifests` |
| `data_manifest.json` | `manifest.py` (at image build) | all of the above and `models/model` | no: paths, sizes, SHA-256, counts |

The bank is not labelled by hand: hand-written templates propose candidate programs for a train question and
`bank.py` keeps a candidate only when running it on the shop's data reproduces `answers.jsonl` exactly.
Only the training split is ever read. The public and private test questions are never used as examples, lexicon
or training data.

## Commands

Run from the code root (`/code` in the image) with the inference environment (`/opt/venv/bin/python`), which already
holds the pinned dependencies of `requirements.txt`. CPU only, no GPU; times measured on a laptop CPU.

```bash
python -X utf8 -m training_code.bank          # data/bank.jsonl + data/missed.jsonl, ~2.5 min for 30 shops
python -X utf8 -m training_code.lexicon       # /code/src/lexicon.json, < 1 min
python -X utf8 -m training_code.hidden_split  # splits/hidden_rules.jsonl
head -n 300 data/train/train/questions.jsonl > training_code/splits/dev300.jsonl
tail -n 300 data/train/train/questions.jsonl > training_code/splits/holdout300.jsonl
python -X utf8 -m training_code.manifest      # data_manifest.json
```

All steps are deterministic: same input files, same outputs.

## Checks

```bash
python -X utf8 -m training_code.verify_fp                                   # verification rules vs. train gold: 0 rejected
python -X utf8 -m src.agent --data data/train/train --out /tmp/oracle \
    --oracle --manifests training_code/manifests                            # replays the bank: PA 0.9757 on train
python scripts/score.py /tmp/oracle/submission.jsonl
```
