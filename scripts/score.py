"""Score a submission.jsonl against answers.jsonl with the DataMate rules (competition/Evaluation.md).

Usage: python scripts/score.py PRED [--gold data/train/train/answers.jsonl] [--questions Q.jsonl] [--show N]
PA = mean(+1 correct, 0 abstain/missing, -1 wrong). Only ids in gold are scored.
"""
import argparse
import collections
import json
import re
import unicodedata

NUM_EPS = 1e-6  # rules say exact match; this only absorbs float noise


def norm(s):
    s = unicodedata.normalize("NFC", str(s)).lower()
    s = re.sub(r"[^\w\s]|_", " ", s)  # \w covers Vietnamese letters + digits
    return " ".join(s.split())


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def num_eq(p, g):
    return is_num(p) and abs(p - g) < NUM_EPS


def str_eq(p, g, aliases=()):
    return isinstance(p, str) and norm(p) in {norm(a) for a in (g, *aliases)}


def cell_eq(p, g, aliases=()):
    return num_eq(p, g) if is_num(g) else str_eq(p, g, aliases)


def list_eq(p, g, aliases):
    if not isinstance(p, list) or len(p) != len(g):
        return False
    aliases = aliases if aliases and len(aliases) == len(g) else [[]] * len(g)
    return all(cell_eq(a, b, al) for a, b, al in zip(p, g, aliases))


def table_eq(p, g, aliases):
    if not isinstance(p, dict) or not isinstance(p.get("rows"), list):
        return False
    pc, gc = p.get("columns") or [], g["columns"]
    if len(pc) != len(gc):
        return False
    # columns by normalized name, else by position
    if sorted(map(norm, pc)) == sorted(map(norm, gc)):
        order = [list(map(norm, pc)).index(norm(c)) for c in gc]
    else:
        order = list(range(len(gc)))
    prows = p["rows"]
    if len(prows) != len(g["rows"]) or not all(isinstance(r, list) and len(r) == len(gc) for r in prows):
        return False
    aliases = aliases if aliases and len(aliases) == len(g["rows"]) else [[]] * len(g["rows"])
    unused = list(range(len(prows)))
    for grow, al in zip(g["rows"], aliases):  # rows as a multiset: greedy match is fine since equality is exact
        hit = next((i for i in unused if all(
            cell_eq(prows[i][order[j]], grow[j], al if j == 0 else ()) for j in range(len(gc)))), None)
        if hit is None:
            return False
        unused.remove(hit)
    return True


def score_one(pred, gold):
    """Return 1 (correct), 0 (abstain/missing), -1 (wrong)."""
    if pred is None or pred.get("type") in (None, "abstain"):
        return 0
    t, g, al = gold["type"], gold.get("answer"), gold.get("alias") or []
    if pred["type"] != t:
        return -1
    p = pred.get("answer")
    ok = {
        "unanswerable": lambda: True,
        "number": lambda: num_eq(p, g),
        "percent": lambda: is_num(p) and round(p, 2) == round(g, 2),
        "string": lambda: str_eq(p, g, al),
        "date": lambda: p == g,
        "boolean": lambda: p is g,
        "list": lambda: list_eq(p, g, al),
        "table": lambda: table_eq(p, g, al),
    }[t]()
    return 1 if ok else -1


def load(path):
    with open(path, encoding="utf8") as f:
        return {r["id"]: r for r in map(json.loads, filter(str.strip, f))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pred")
    ap.add_argument("--gold", default="data/train/train/answers.jsonl")
    ap.add_argument("--questions", help="only score ids in this file (e.g. a dev subset)")
    ap.add_argument("--show", type=int, default=10, help="print N wrong answers")
    a = ap.parse_args()

    gold, pred = load(a.gold), load(a.pred)
    ids = list(load(a.questions)) if a.questions else list(gold)
    s = {i: score_one(pred.get(i), gold[i]) for i in ids}
    n = len(s)
    c = collections.Counter(s.values())
    print(f"PA = {sum(s.values()) / n:.4f}  (n={n}, correct={c[1]}, wrong={c[-1]}, abstain={c[0]})")

    by_type = collections.defaultdict(list)
    for i, v in s.items():
        by_type[gold[i]["type"]].append(v)
    for t, vs in sorted(by_type.items()):
        k = collections.Counter(vs)
        print(f"  {t:13s} n={len(vs):4d}  PA={sum(vs) / len(vs):+.3f}  ok={k[1]} wrong={k[-1]} abstain={k[0]}")

    wrong = [i for i, v in s.items() if v == -1][: a.show]
    for i in wrong:
        g = gold[i]
        print(f"- {i}: gold={json.dumps(g.get('answer'), ensure_ascii=False)[:150]} ({g['type']})"
              f" pred={json.dumps(pred[i].get('answer'), ensure_ascii=False)[:150]} ({pred[i].get('type')})")


if __name__ == "__main__":
    main()
