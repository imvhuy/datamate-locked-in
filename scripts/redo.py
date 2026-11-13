"""Re-run only the answers a finished run would now reject, instead of the whole split (dev only, saves GPU time).

    python scripts/redo.py pick  LOG_DIR DATA OUT       # replay each logged program with today's checks
    python scripts/redo.py merge SUBMISSION REDO OUT    # SUBMISSION with the lines of REDO put in place

`pick` rebuilds every shop with the manifest the run logged (LOG_DIR/schema.jsonl), runs the logged program again
and keeps the questions whose result src.agent would now send back to the model (check before running, the
checks of src.verify after). Those are the only questions whose answer the current code can change.
"""
import json
import sys

sys.path.insert(0, ".")
from src.agent import check, run_program
from src.load import load_shop
from src.metrics import IMPLEMENTS, OPTIONAL, Metrics
from src.period import read_lunar
from src.schema import guess_manifest, read_readme
from src.shop import apply_patch, build
from src.verify import after_run


def read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def pick(log_dir, data, out):
    logged = {r["id"]: r for r in read(f"{log_dir}/log.jsonl")}
    manifests = {r["business_id"]: r["manifest"] for r in read(f"{log_dir}/schema.jsonl")}
    with open(f"{data}/rulebook.md", encoding="utf-8") as f:
        text = f.read()
    hidden = frozenset(fn for fn in OPTIONAL if IMPLEMENTS[fn] not in text)
    lunar = read_lunar(f"{data}/calendar.csv")
    questions = read(f"{data}/questions.jsonl")
    redo = []
    for biz in dict.fromkeys(q["business_id"] for q in questions):
        tables, readme = load_shop(f"{data}/data/{biz}"), read_readme(f"{data}/data/{biz}")
        manifest = guess_manifest(tables, readme)
        if biz in manifests:
            apply_patch(manifest, manifests[biz])
        shop = build(tables, manifest, readme)
        mine = 0
        for q in (q for q in questions if q["business_id"] == biz):
            r = logged.get(q["id"])
            if r is None or not r.get("code") or r["answer"]["type"] == "abstain":
                continue
            typ = r["answer"]["type"] if r["answer"]["type"] != "unanswerable" else "number"
            problems = check(r["code"], q["question"], shop)
            if not problems:
                m, used = Metrics(shop, q["as_of_date"], lunar), []
                answer, _ = run_program(typ, r["code"], m, hidden, used)
                problems = after_run(answer if r["answer"]["type"] != "unanswerable" else r["answer"], q["question"], m, used, r["code"])
            if problems:
                redo.append(q)
                mine += 1
                print(f"  {biz} {q['question'][:70]!r}: {problems[0][:110]}")
        print(f"{biz}: {mine} to redo", flush=True)
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(q, ensure_ascii=False) + "\n" for q in redo)
    print(len(redo), "questions to redo ->", out)


def merge(submission, redo, out):
    new = {r["id"]: r for r in read(redo)}
    recs = [new.pop(r["id"], r) for r in read(submission)]
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
    print(len(recs), "lines,", len(read(redo)) - len(new), "replaced ->", out)


if __name__ == "__main__":
    {"pick": pick, "merge": merge}[sys.argv[1]](*sys.argv[2:5])
