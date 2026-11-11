"""Build the verified example bank: (train question -> program) pairs whose result equals the reference answer.

Hand-written templates propose candidate programs for a question; a candidate is kept only if running it on the
shop's data reproduces answers.jsonl exactly. No manual labels. The bank is (a) the few-shot examples the agent
retrieves, (b) fine-tuning data, (c) a regression test of src/metrics.py.

    python -X utf8 -m training_code.bank [biz_105 biz_107 ...]     # default: every train shop

Programs are Python expressions over `m` (src.metrics.Metrics) and `P` (= m.P, src.period.Periods).
"""
import collections
import json
import os
import pickle
import sys

from scripts.score import score_one
from src.load import load_shop
from src.metrics import Metrics, NoData, is_no_data
from src.parse import fold
from src.period import read_lunar
from src.shop import open_shop
from training_code.templates import candidates, find_entities, find_periods

SPLIT = "data/train/train"
OUT = "training_code/data/bank.jsonl"
NOT_RECORDED = 'no_data("dữ liệu của shop không ghi thông tin này")'
ANSWER_TYPES = ["number", "percent", "string", "list", "date", "table", "boolean"]


def cached_tables(shop_dir):
    """load_shop, pickled until src/load.py or src/parse.py change (loading is the slow part)."""
    cache = f"training_code/data/.cache/{os.path.basename(shop_dir)}.pkl"
    newest = max(os.path.getmtime(p) for p in ("src/load.py", "src/parse.py"))
    if os.path.exists(cache) and os.path.getmtime(cache) > newest:
        with open(cache, "rb") as f:
            return pickle.load(f)
    tables = load_shop(shop_dir)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "wb") as f:
        pickle.dump(tables, f)
    return tables


def cached_shop(shop_dir):
    """Shop with the guessed manifest, patched by training_code/manifests/<shop>.json when there is one."""
    patch = f"training_code/manifests/{os.path.basename(shop_dir)}.json"
    return open_shop(shop_dir, patch=patch, tables=cached_tables(shop_dir))


def run(shops):
    with open(f"{SPLIT}/questions.jsonl", encoding="utf-8") as f:
        questions = [json.loads(x) for x in f if x.strip()]
    with open(f"{SPLIT}/answers.jsonl", encoding="utf-8") as f:
        gold = {r["id"]: r for r in map(json.loads, filter(str.strip, f))}
    lunar = read_lunar(f"{SPLIT}/calendar.csv")
    bank, missed = [], []
    stats, misses = collections.defaultdict(collections.Counter), collections.defaultdict(list)
    for biz in shops:
        shop = cached_shop(f"{SPLIT}/data/{biz}")
        for q in (q for q in questions if q["business_id"] == biz):
            g = gold[q["id"]]
            f = " ".join(fold(q["question"]).split())
            periods, ents = find_periods(f), find_entities(shop, q["question"])
            # A reference "unanswerable" has no answer type: try what any type would run.
            types = [g["type"]] if g["type"] != "unanswerable" else ANSWER_TYPES
            progs = list(dict.fromkeys(x for t in types for x in candidates(f, t, periods, ents)))
            m = Metrics(shop, q["as_of_date"], lunar)
            hit, tried = None, []
            for prog in progs:
                try:
                    val = eval(prog, {"m": m, "P": m.P})
                except (NoData, KeyError, TypeError, ValueError, AttributeError, ZeroDivisionError, IndexError) as e:
                    if g["type"] == "unanswerable" and is_no_data(e):  # the code itself says the data lacks it
                        hit = prog
                        break
                    tried.append((prog, f"{type(e).__name__}: {e}"))
                    continue
                # A value is never right for a reference "unanswerable": only a program that raises NoData is.
                if g["type"] != "unanswerable" and score_one({"type": g["type"], "answer": val}, g) == 1:
                    hit = prog
                    break
                tried.append((prog, val))
            if not hit and g["type"] == "unanswerable":  # nothing the shop records: gender, address, reasons...
                hit = NOT_RECORDED
            stats[biz]["hit" if hit else "wrong" if progs else "no_template"] += 1
            if hit:
                bank.append({"id": q["id"], "business_id": biz, "as_of_date": q["as_of_date"],
                             "question": q["question"], "type": g["type"], "program": hit})
            else:
                if progs:
                    misses[biz].append((q["question"], g.get("answer"), tried[:2]))
                missed.append({"id": q["id"], "business_id": biz, "question": q["question"], "type": g["type"],
                               "answer": g.get("answer"), "tried": [[a, str(b)] for a, b in tried[:3]]})
        s = stats[biz]
        print(f"{biz}: hit={s['hit']} wrong={s['wrong']} no_template={s['no_template']}  notes={shop.notes}", flush=True)
        for question, want, tried in misses[biz][:4]:
            print(f"    MISS {question[:70]!r} want={want} tried={tried}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    for path, rows in ((OUT, bank), (OUT.replace("bank", "missed"), missed)):
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tot = collections.Counter()
    for s in stats.values():
        tot.update(s)
    print(f"TOTAL hit={tot['hit']} wrong={tot['wrong']} no_template={tot['no_template']} -> {OUT}")


if __name__ == "__main__":
    run(sys.argv[1:] or sorted(os.listdir(f"{SPLIT}/data")))
