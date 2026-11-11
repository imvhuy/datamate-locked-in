"""Answer every question of a split.

For each question a local model writes a short program over `m` (src.metrics.Metrics), `P` (periods) and `T`
(the shop's cleaned tables); we run it and format the result. Numbers always come from code, never from the model.

    python -X utf8 -m src.agent --data data/train/train --out out --limit 300 --llm http://localhost:8000/v1
    python -X utf8 -m src.agent --data data/train/train --out out --oracle      # no model: replay the bank

Writes <out>/submission.jsonl (one line per question, flushed at once) and <out>/logs/log.jsonl with
id, answer, elapsed_ms, thinking, code, output for every question (Docker guideline 3.3). The model's schema
mapping of each shop is logged to <out>/logs/schema.jsonl.
"""
import argparse
import ast
import collections
import concurrent.futures
import inspect
import json
import math
import os
import re
import threading
import time
import urllib.request
import zlib

import pandas as pd

from src.load import load_shop
from src.mapper import complete_manifest, needs_model, table_card
from src.metrics import Metrics, NoData, half_up, is_no_data, tidy
from src.parse import fold
from src.period import Periods, read_lunar
from src.schema import guess_manifest, read_readme
from src.shop import apply_patch, build, key

BANK = os.path.join(os.path.dirname(__file__), "..", "training_code", "data", "bank.jsonl")
TYPES = ["number", "percent", "string", "date", "boolean", "list", "table"]
SYSTEM = """You answer Vietnamese questions about one small shop's sales data by writing a short Python program.
You never compute numbers yourself: the program does, through the objects documented below.

Reply with the answer type on one line, then one Python code block, and nothing else. Two complete replies:

type: number
```python
answer = m.revenue(P.month(2025, 7), prod="Sting dâu 330ml")
```

type: percent
```python
answer = m.pct_change(m.revenue(P.this_month()), m.revenue(P.same_days_last_month()))
```

The type is one of number, percent, string, date, boolean, list, table. Write real values taken from the question
and from this shop's data (product names exactly as listed, real dates), never placeholders. The solved examples
come from other shops and other days: copy their method, never their periods, years or names.

Rules:
- `P` builds periods relative to the question's "today"; pass a period to every metric. Never write dates by hand
  when a P method exists (P.this_month(), P.last_week(), P.month(2025, 7), P.tet(2026) ...).
- Prefer `m`: its methods already apply the rulebook (valid orders, base units, rounding). Use the raw tables `T`
  only for what `m` does not cover, such as columns special to this shop's line of business. `T["<table>"]` is a
  pandas DataFrame with the original column names, dates and numbers already parsed. Blank cells on later rows
  of a group inherit the first row (rulebook: cách ghi trong file); `m.rows(df, "<date column>", period)` keeps
  the rows of a period and drops anything after today. Helpers: key(text) compares names ignoring case and
  accents, half_up(x, 2) rounds the rulebook way, pd is pandas.
- The rulebook below defines every business term. When the question does not say otherwise, the rulebook decides.
- If the shop's data cannot answer (the information is not recorded, or it needs outside knowledge), write
  `answer = no_data("<short reason>")`. Do not guess and do not return 0 for missing data.
- type: money, counts and quantities are `number`; percentages are `percent`; a name is `string`; a day is `date`
  ("YYYY-MM-DD", a month "YYYY-MM"); several values in the order the question asks are `list`.
"""


def api_doc():
    """Signatures and one-line docs of what a program may call."""
    lines = []
    for name, cls in (("m", Metrics), ("P", Periods)):
        for fn, obj in inspect.getmembers(cls, inspect.isfunction):
            if not fn.startswith("_"):
                sig = str(inspect.signature(obj)).replace("(self, ", "(").replace("(self)", "()")
                doc = (inspect.getdoc(obj) or "").split("\n")[0]
                lines.append(f"{name}.{fn}{sig}" + (f"  # {doc}" if doc else ""))
    return inspect.getdoc(inspect.getmodule(Metrics)) + "\n\n" + "\n".join(lines)


class Examples:
    """Verified (question -> program) pairs from the bank, retrieved by BM25 on spelling-insensitive words."""

    def __init__(self, path=BANK):
        with open(path, encoding="utf-8") as f:
            self.rows = [json.loads(x) for x in f if x.strip()]
        self.docs = [key(r["question"]).split() for r in self.rows]
        df = collections.Counter(w for d in self.docs for w in set(d))
        self.idf = {w: math.log(1 + (len(self.docs) - n + 0.5) / (n + 0.5)) for w, n in df.items()}
        self.avg = sum(map(len, self.docs)) / len(self.docs)

    def top(self, question, k=8, skip_shop=None):
        q = set(key(question).split())
        scored = []
        for row, doc in zip(self.rows, self.docs):
            if row["business_id"] == skip_shop:  # measuring on train: never show the shop's own questions
                continue
            tf = collections.Counter(doc)
            s = sum(self.idf.get(w, 0) * tf[w] * 2.5 / (tf[w] + 1.5 * (0.25 + 0.75 * len(doc) / self.avg)) for w in q if w in tf)
            scored.append((s, row["id"], row))
        return [r for _, _, r in sorted(scored, key=lambda x: (-x[0], x[1]))[:k]]


def shop_card(shop):
    """What this shop records: its README, each table with sample values, and the values filters accept."""
    out = [f"### README\n{shop.readme['text'].strip()}", "### Tables (use as T[\"<name>\"]; \"-> field\" is the canonical meaning)"]
    out += [table_card(n, shop.tables[n], spec, values=3) for n, spec in shop.manifest["tables"].items()]
    lines = shop.lines if shop.lines is not None else shop.orders
    vals = []
    for label, col in (("group", "group"), ("cust_group", "cust_group"), ("base units", "base_unit"),
                       ("pay", "pay"), ("chan", "chan"), ("staff", "staff")):  # fmt: skip
        if lines is not None and col in lines:
            vals.append(f"{label}: {sorted(map(str, lines[col].dropna().unique()))[:40]}")
    return "\n\n".join(out) + "\n\n### Filter values of m\n" + "\n".join(vals + [f"note: {n}" for n in shop.notes])


def linked(shop, question, k=8):
    """Catalogue products that share the most words with the question (exact spellings to copy)."""
    if shop.products is None or "prod_name" not in shop.products:
        return []
    q = set(key(question).split())
    scored = [(len(q & set(str(p).split())) / (len(str(p).split()) or 1), n) for n, p in zip(shop.products["prod_name"], shop.products["pkey"])]
    return [n for s, n in sorted(scored, key=lambda x: (-x[0], str(x[1])))[:k] if s >= 0.4]


def build_messages(static, card, examples, question, as_of, products):
    shots = "\n\n".join(f"Q: {r['question']}\ntype: {r['type']}\nanswer = {r['program']}" for r in examples)
    user = (f"## This shop\n{card}\n\n## Similar solved questions (other shops)\n{shots}\n\n"
            f"## Products that may be meant\n{products}\n\n## Question (today is {as_of})\n{question}")  # fmt: skip
    return [{"role": "system", "content": static}, {"role": "user", "content": user}]


def split_thinking(msg, finish):
    """(answer text, thinking or None) from a chat message, whether or not the server separated the reasoning.

    With a reasoning parser vLLM puts it in `reasoning` (older: `reasoning_content`). Without one the content holds
    "...thinking...</think>answer" (Qwen3.5 opens the block in the prompt, so there may be no "<think>"); a reply cut
    by max_tokens before "</think>" is all thinking.
    """
    text, thinking = msg.get("content") or "", msg.get("reasoning") or msg.get("reasoning_content")
    if thinking is None and "</think>" in text:
        head, _, text = text.rpartition("</think>")
        thinking = head.replace("<think>", "", 1).strip()
    elif thinking is None and finish == "length" and not re.search(r"```|^type\s*:", text, re.MULTILINE):
        thinking, text = text.replace("<think>", "", 1).strip(), ""
    return text.strip(), thinking


def call_llm(url, model, messages, think=True, max_tokens=6144):
    """One deterministic chat completion from an OpenAI-compatible server -> (text, thinking or None, cut short)."""
    body = {"model": model, "messages": messages, "temperature": 0, "seed": 0, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": think}}  # fmt: skip
    req = urllib.request.Request(f"{url}/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        choice = json.load(r)["choices"][0]
    text, thinking = split_thinking(choice["message"], choice.get("finish_reason"))
    return text, thinking, choice.get("finish_reason") == "length"


def parse_reply(text):
    """-> (type, code) or (None, None) when the reply does not follow the form.

    Lenient on layout: the type line may sit inside the code block, the code may be split over several blocks,
    a copied "Q: ..." example line is dropped, and an "answer = ..." line outside any block is accepted.
    """
    typ = re.search(r"type\s*:\s*`?(\w+)", text)
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.DOTALL)
    code = "\n".join(x for b in blocks for x in b.splitlines() if not re.match(r"\s*(?:type|Q)\s*:", x)).strip()
    if not code and (line := re.search(r"^answer\s*=.*$", text, re.MULTILINE)):
        code = line[0]
    if not typ or typ[1] not in TYPES or not code:
        return None, None
    return typ[1], code


PLACEHOLDER = re.compile(r"<[^<>\n]{2,40}>|product[ _]?name|YYYY|MM-DD", re.IGNORECASE)
MONTH_RX = re.compile(r"(?:\b(?:thang|th|t)\s*\.?\s*|\b)(\d{1,2})\s*(?:/|-|\s+nam\s+)\s*(\d{4}|\d{2})\b")


def mentioned(text, question):
    """True when most words of `text` occur in the question (accents and case ignored)."""
    words, q = key(text).split(), set(key(question).split())
    return bool(words) and sum(w in q for w in words) / len(words) >= 0.5


PRODUCT_ARG = {"stock": 0, "stock_split": 0, "price_on": 0, "price_raise_day": 0, "avg_purchase_price": 1, "purchased_qty": 1}


def _product_args(node):
    """String constants a call passes where a product is expected (prod=..., or a product parameter by position)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return []
    found = [k.value for k in node.keywords if k.arg == "prod"]
    i = PRODUCT_ARG.get(node.func.attr)
    if i is not None and len(node.args) > i:
        found.append(node.args[i])
    return [x.value for x in found if isinstance(x, ast.Constant) and isinstance(x.value, str)]


def check(code, question, shop):
    """Problems a program has before it runs: placeholders, and names or months that are not in the question.

    Catches the commonest failure of a small model: copying a solved example's product, group or period.
    """
    if m := PLACEHOLDER.search(code):
        return [f"`{m[0]}` is a placeholder: write the real value from the question"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e.msg}"]
    problems, fq = [], fold(question)
    months = {(int(y) + 2000 if len(y) == 2 else int(y), int(mo)) for mo, y in MONTH_RX.findall(fq) if 1 <= int(mo) <= 12}
    years = {int(y) for y in re.findall(r"\b(20\d\d)\b", fq)} | {y for y, _ in months}
    if re.search(r"nam (?:truoc|ngoai|roi)|cung ky", fq):  # "cùng kỳ năm trước" names this year, means last year too
        years |= {y - 1 for y in years}
        months |= {(y - 1, mo) for y, mo in months}
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "month"]
    if len(calls) > 2:  # a run of months ("từ tháng 8 đến tháng 10", "3 tháng kết thúc ở tháng 12"): check years only
        months = set()
    for node in ast.walk(tree):
        is_text = isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 3
        if is_text and shop.find_prod(node.value) is not None and not mentioned(node.value, question):
            problems.append(f"the question does not mention the product '{node.value}'")
        for arg in _product_args(node):
            if shop.products is not None and shop.find_prod(arg) is None:
                problems.append(f"'{arg}' is not a product of this shop: copy a name from the products list")
        is_named = isinstance(node, ast.keyword) and node.arg in ("group", "cust_group", "ingredient")
        if is_named and isinstance(node.value, ast.Constant) and not mentioned(str(node.value.value), question):
            problems.append(f"the question does not mention {node.arg} '{node.value.value}'")
        is_period = isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("month", "quarter", "year")
        if is_period and node.args and all(isinstance(x, ast.Constant) and isinstance(x.value, int) for x in node.args):
            vals = tuple(x.value for x in node.args)
            if years and vals[0] not in years:
                problems.append(f"P.{node.func.attr}{vals}: the question names the year(s) {sorted(years)}")
            elif node.func.attr == "month" and months and vals[:2] not in months:
                problems.append(f"P.month{vals}: the question names the month(s) {sorted(months)}")
    return problems


def no_data(reason=""):
    e = NoData(reason)
    e.explicit = True  # the model's own judgement, not a missing column
    raise e


def plain(v):
    """numpy / pandas scalars -> JSON values; numbers tidied the rulebook way."""
    if isinstance(v, (bool, str)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "tolist") and not isinstance(v, (int, float)):
        v = v.tolist()
        return plain(v) if isinstance(v, list) else (bool(v) if isinstance(v, bool) else tidy(v))
    return tidy(v)


def shaky(shop):
    """True when "the shop does not record X" cannot be trusted: sales unreadable, or some column of a table the
    README does not describe was never understood (it may be exactly the missing group, cost or customer type)."""
    tables = shop.manifest["tables"].values()
    if shop.manifest.get("by_model"):  # the model filled in the schema: a missing column may be a column it missed
        return True
    return (shop.lines is None and shop.orders is None) or any(t["unknown"] and not t.get("documented") for t in tables)


def as_program(code):
    """Model code -> code that assigns `answer`: stray "type:" lines go, a final bare expression becomes the answer."""
    code = "\n".join(x for x in code.splitlines() if not re.match(r"\s*type\s*:", x))
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code  # exec will report it
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        tree.body[-1] = ast.Assign(targets=[ast.Name("answer", ast.Store())], value=last.value, lineno=last.lineno)
        return ast.unparse(ast.fix_missing_locations(tree))
    return code


def run_program(typ, code, m):
    """Execute a program -> (submission dict, raw output). Errors become unanswerable or abstain."""
    ns = {"m": m, "P": m.P, "T": {n: t.df for n, t in m.shop.tables.items()}, "pd": pd, "key": key,
          "half_up": half_up, "no_data": no_data}  # fmt: skip
    try:
        exec(as_program(code), ns)  # noqa: S102 - the program comes from our own offline model
        if "answer" not in ns:
            return {"type": "abstain"}, "the program set no answer"
        value = plain(ns["answer"])
    except Exception as e:  # noqa: BLE001 - any failure of generated code must end in a safe answer
        # "The data lacks it" is an answer only when we trust our reading of the shop; a period outside the data
        # and the model's own no_data() are always trusted.
        sure = getattr(e, "explicit", False) or "wholly outside" in str(e) or not shaky(m.shop)
        if is_no_data(e) and sure:
            return {"type": "unanswerable", "reason": str(e)}, repr(e)
        return {"type": "abstain"}, repr(e)
    ok = {"number": isinstance(value, (int, float)) and not isinstance(value, bool), "percent": isinstance(value, (int, float)),
          "string": isinstance(value, str), "date": isinstance(value, str), "boolean": isinstance(value, bool),
          "list": isinstance(value, list), "table": isinstance(value, dict)}[typ]  # fmt: skip
    if not ok or (isinstance(value, float) and math.isnan(value)):  # the wrong shape is a wrong answer: stay silent
        return {"type": "abstain"}, value
    return {"type": typ, "answer": value}, value


def open_for_run(shop_dir, ask=None, patch=None):
    """Load a shop; when the rules cannot read its schema and a model is there, let the model finish the manifest.

    -> (shop, the model's raw mapping reply or None). `patch` is a development-only manifest patch.
    """
    tables, readme = load_shop(shop_dir), read_readme(shop_dir)
    manifest, reply = guess_manifest(tables, readme), None
    if patch and os.path.exists(patch):
        apply_patch(manifest, patch)
    elif ask and needs_model(manifest):
        manifest, reply = complete_manifest(tables, readme, manifest, ask)
        manifest["by_model"] = True
    return build(tables, manifest, readme), reply


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="split folder: questions.jsonl, rulebook.md, calendar.csv, data/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--llm", default="http://localhost:8000/v1", help="server URL, or several separated by commas")
    ap.add_argument("--model", default="model")
    ap.add_argument("--limit", type=int, help="first N questions only")
    ap.add_argument("--questions", help="question file to use instead of <data>/questions.jsonl (a dev or held-out split)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-think", action="store_true")
    ap.add_argument("--oracle", action="store_true", help="replay bank programs instead of calling a model")
    ap.add_argument("--same-shop", action="store_true", help="allow examples from the question's own shop")
    ap.add_argument("--manifests", help="DEV ONLY: folder of manifest patches for train shops (never in predict.sh)")
    a = ap.parse_args()

    with open(a.questions or f"{a.data}/questions.jsonl", encoding="utf-8") as f:
        questions = [json.loads(x) for x in f if x.strip()][: a.limit]
    with open(f"{a.data}/rulebook.md", encoding="utf-8") as f:
        static = f"{SYSTEM}\n## API\n{api_doc()}\n\n## Rulebook\n{f.read()}"
    lunar = read_lunar(f"{a.data}/calendar.csv")
    bank = Examples()
    by_id = {r["id"]: r for r in bank.rows}
    os.makedirs(f"{a.out}/logs", exist_ok=True)
    urls = a.llm.split(",")

    def ask(messages):
        text, _, cut = call_llm(urls[0], a.model, messages, think=not a.no_think, max_tokens=8192)
        if cut or not text:  # thinking ran out of room before the JSON
            text = call_llm(urls[0], a.model, messages, think=False, max_tokens=4096)[0]
        return text

    def solve(q, shop, card):
        t0 = time.time()
        thinking = None
        if a.oracle:
            row = by_id.get(q["id"])
            typ, code = (row["type"], f"answer = {row['program']}") if row else (None, None)
            if typ == "unanswerable":  # the bank program raises NoData
                typ = "number"
            answer, output = run_program(typ, code, Metrics(shop, q["as_of_date"], lunar)) if code else ({"type": "abstain"}, None)
            return {"id": q["id"], "answer": answer, "elapsed_ms": int((time.time() - t0) * 1000), "thinking": thinking,
                    "code": code, "output": output}  # fmt: skip
        shots = bank.top(q["question"], skip_shop=None if a.same_shop else q["business_id"])
        msgs = build_messages(static, card, shots, q["question"], q["as_of_date"], linked(shop, q["question"]))
        url = urls[zlib.crc32(q["id"].encode()) % len(urls)]  # same question, same server, every run
        thoughts, tries, think = [], [], not a.no_think
        for _ in range(2):  # one retry, told exactly what was wrong
            text, th, cut = call_llm(url, a.model, msgs, think=think)
            thoughts.append(th)
            typ, code = parse_reply(text)
            problems = check(code, q["question"], shop) if code else ["the reply must be a type line and one python block"]
            if cut and not code:  # thought until the token limit: the retry answers without the thinking phase
                think, text = False, "(no answer: the reasoning ran past the length limit)"
            answer, output = {"type": "abstain"}, None
            if not problems:
                answer, output = run_program(typ, code, Metrics(shop, q["as_of_date"], lunar))
                if answer["type"] == "abstain":
                    problems = [f"running it failed: {output}"]
            tries.append({"code": code, "problems": problems})
            if not problems:
                break
            fix = ("Your program has a problem: " + "; ".join(problems)
                   + ". Take every product, group, month and year from the question. Reply again in the same form.")  # fmt: skip
            msgs = [*msgs, {"role": "assistant", "content": text}, {"role": "user", "content": fix}]
        return {"id": q["id"], "answer": answer, "elapsed_ms": int((time.time() - t0) * 1000),
                "thinking": thoughts[0] if len(thoughts) == 1 else thoughts, "code": code, "output": output,
                "tries": tries}  # fmt: skip

    # The next shop loads (CPU) while the model answers the previous one (GPU); at most 3 shops are in memory.
    # Answers are written the moment they exist, in whatever order they finish (Docker guideline 3.2).
    lock, room, start = threading.Lock(), threading.Semaphore(3), time.time()
    done, futures = [0], []
    with (
        open(f"{a.out}/submission.jsonl", "w", encoding="utf-8") as sub,
        open(f"{a.out}/logs/log.jsonl", "w", encoding="utf-8") as log,
        open(f"{a.out}/logs/schema.jsonl", "w", encoding="utf-8") as schema,
        concurrent.futures.ThreadPoolExecutor(a.workers) as pool,
    ):

        def write(fut, biz, left):
            rec = fut.result()
            with lock:
                sub.write(json.dumps({"id": rec["id"], **rec["answer"]}, ensure_ascii=False) + "\n")
                log.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                sub.flush()
                log.flush()
                done[0] += 1
                left[0] -= 1
                if left[0] == 0:
                    room.release()
                    print(f"{biz} done: {done[0]}/{len(questions)} questions, {time.time() - start:.0f}s", flush=True)

        for biz in dict.fromkeys(q["business_id"] for q in questions):
            room.acquire()
            patch = a.manifests and f"{a.manifests}/{biz}.json"
            shop, reply = open_for_run(f"{a.data}/data/{biz}", None if a.oracle else ask, patch)
            card = shop_card(shop)
            schema.write(json.dumps({"business_id": biz, "manifest": shop.manifest, "model_reply": reply,
                                     "notes": shop.notes}, ensure_ascii=False, default=str) + "\n")  # fmt: skip
            schema.flush()
            mine = [q for q in questions if q["business_id"] == biz]
            left = [len(mine)]
            for q in mine:
                fut = pool.submit(solve, q, shop, card)
                fut.add_done_callback(lambda f, b=biz, n=left: write(f, b, n))
                futures.append(fut)
        for fut in futures:
            fut.result()  # surface any error raised inside a worker


if __name__ == "__main__":
    main()
