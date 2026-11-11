"""Check a submission.jsonl against the format rules (rulebook §9) before uploading it.

    python scripts/check_submission.py SUBMISSION QUESTIONS

Exit code 1 and a list of problems when something would be read as wrong or missing; prints a summary either way.
"""
import collections
import json
import re
import sys

DATE = re.compile(r"\d{4}-\d{2}(-\d{2})?")


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def problem(rec):
    t, a = rec.get("type"), rec.get("answer")
    if t in ("abstain", "unanswerable"):
        return None if t == "abstain" or isinstance(rec.get("reason", ""), str) else "reason must be a string"
    ok = {
        "number": is_num(a),
        "percent": is_num(a),
        "string": isinstance(a, str) and a.strip() != "",
        "date": isinstance(a, str) and DATE.fullmatch(a) is not None,
        "boolean": isinstance(a, bool),
        "list": isinstance(a, list) and all(isinstance(x, str) or is_num(x) for x in a),
        "table": isinstance(a, dict) and isinstance(a.get("columns"), list) and isinstance(a.get("rows"), list)
        and all(isinstance(r, list) and len(r) == len(a["columns"]) for r in a["rows"]),
    }.get(t)
    if ok is None:
        return f"unknown type {t!r}"
    return None if ok else f"{t} answer has the wrong shape: {json.dumps(a, ensure_ascii=False)[:80]}"


def main(sub_path, q_path):
    with open(q_path, encoding="utf-8") as f:
        ids = [json.loads(x)["id"] for x in f if x.strip()]
    with open(sub_path, encoding="utf-8") as f:
        recs = [json.loads(x) for x in f if x.strip()]
    errors = []
    seen = collections.Counter(r.get("id") for r in recs)
    errors += [f"duplicate id {i}" for i, n in seen.items() if n > 1]
    errors += [f"id not in the questions: {i}" for i in seen if i not in set(ids)]
    missing = [i for i in ids if i not in seen]
    errors += [f"{r['id']}: {p}" for r in recs if (p := problem(r))]
    types = collections.Counter(r.get("type") for r in recs)
    print(f"{len(recs)} lines for {len(ids)} questions, missing {len(missing)} (scored as abstain)")
    print("types:", dict(types.most_common()))
    for e in errors[:30]:
        print("PROBLEM", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
