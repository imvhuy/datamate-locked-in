"""Build the "new rule" measurement set: train questions whose solution needs a method we then hide.

    python -X utf8 -m training_code.hidden_split      # -> training_code/splits/hidden_rules.jsonl

Run the agent on it with `--hide` set to HIDE: each question then needs a rule no method implements, which is what
an unseen rulebook row looks like at test time, while the train answers still score every reply. Questions of
holdout300 are left out, so this set can be read question by question without touching the held-out score.
"""
import json
import random
import re

BANK = "training_code/data/bank.jsonl"  # no heavy imports: a notebook kernel without the data libraries reads HIDE

HIDE = ["cancel_rate", "best_day", "price_on", "avg_purchase_price", "unsold", "rising", "top_debtors",
        "n_deliveries", "stock_split", "debt_aging", "top_staff", "top_suppliers", "price_raise_day"]  # fmt: skip
PER_METHOD = 20
SPLITS = "training_code/splits"


def main():
    with open(f"{SPLITS}/holdout300.jsonl", encoding="utf-8") as f:
        held = {json.loads(x)["id"] for x in f if x.strip()}
    with open(BANK, encoding="utf-8") as f:
        bank = [json.loads(x) for x in f if x.strip()]
    with open("data/train/train/questions.jsonl", encoding="utf-8") as f:
        questions = {q["id"]: q for q in map(json.loads, filter(str.strip, f))}
    rng, chosen = random.Random(0), {}
    for name in HIDE:
        rows = sorted((r for r in bank if re.search(rf"\bm\.{name}\(", r["program"]) and r["id"] not in held),
                      key=lambda r: r["id"])  # fmt: skip
        for r in rng.sample(rows, min(PER_METHOD, len(rows))):
            chosen.setdefault(r["id"], name)
    with open(f"{SPLITS}/hidden_rules.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(questions[qid], ensure_ascii=False) + "\n" for qid in sorted(chosen))
    print(f"{len(chosen)} questions -> {SPLITS}/hidden_rules.jsonl; run with --hide {','.join(HIDE)}")


if __name__ == "__main__":
    main()
