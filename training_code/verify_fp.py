"""False alarms of src/verify.py on train: every gold answer it rejects is a rule to fix or drop.

    python -X utf8 -m training_code.verify_fp [biz_105 ...]
"""
import collections
import json
import os
import sys

from src.metrics import Metrics
from src.period import read_lunar
from src.verify import result_problems
from training_code.bank import SPLIT, cached_shop


def main(shops):
    with open(f"{SPLIT}/questions.jsonl", encoding="utf-8") as f:
        questions = [json.loads(x) for x in f if x.strip()]
    with open(f"{SPLIT}/answers.jsonl", encoding="utf-8") as f:
        gold = {r["id"]: r for r in map(json.loads, filter(str.strip, f))}
    lunar = read_lunar(f"{SPLIT}/calendar.csv")
    n, bad = 0, collections.Counter()
    for biz in shops:
        shop = cached_shop(f"{SPLIT}/data/{biz}")
        for q in (q for q in questions if q["business_id"] == biz):
            g = gold[q["id"]]
            if g["type"] == "unanswerable":
                continue
            n += 1
            probs = result_problems(g, q["question"], Metrics(shop, q["as_of_date"], lunar))
            if probs:
                bad[probs[0][:40]] += 1
                print(f"FALSE ALARM {biz} {q['question'][:80]!r} gold={json.dumps(g.get('answer'), ensure_ascii=False)[:80]}"
                      f" -> {probs[0][:120]}")  # fmt: skip
    print(f"{n} gold answers, {sum(bad.values())} rejected: {dict(bad)}")


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(os.listdir(f"{SPLIT}/data")))
