# Train splits used for measuring

- `dev300.jsonl`: the first 300 lines of `data/train/train/questions.jsonl`. Used to find and fix errors: we read its
  failures and change code because of them, so its score is optimistic.
- `holdout300.jsonl`: the last 300 lines. Used only to report a score. Nobody reads its individual failures to fix
  code; if that ever happens, it stops being a holdout and a new one must be drawn.

Both are question files for `python -m src.agent --questions <file>`; score with `scripts/score.py --questions <file>`.
