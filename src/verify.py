"""Checks on a result that ran: what makes it almost surely wrong, read from the question and the shop only.

Each rule is kept only if it rejects no train gold answer (python -X utf8 -m training_code.verify_fp). A rejected
result goes back to the model once with the reason; if the retry is rejected too, the question is left blank
(0 points) instead of a likely wrong answer (−1).
"""
import ast
import datetime as dt
import re

from src.asked import asked, period_problems
from src.parse import fold
from src.shop import key

ZERO = ("the result is 0: a filter likely matched nothing. Look at how the data writes the values you filter on "
        "(names, groups, the words in a text column) and match them loosely; keep only words the data uses")
TAIL = re.compile(r"\([^)]*\)|\d[\d .-]{6,}\d")
UNSOLD = re.compile(r"\b(?:e|khong ai mua|ko ai mua|khong ban|ko ban|chua ban|khong co ai)\b")
# "so" alone is also "số" once accents are gone: only "so với", "so cùng kỳ", "so tháng trước"... compare
CHANGE = re.compile(r"\b(?:tang|giam|thay doi|so (?:voi|vs|cung|thang|th|ky|nam|tuan|ngay|quy|lien|truoc|hom)|hon|kem"
                    r"|xuong|chenh|lech|bien dong|lai|lo)\b")
SHARE = re.compile(r"\b(?:chiem|ti trong|ty trong)\b")
WHO = re.compile(r"\b(?:ai|khach nao|hang nao|mat hang nao|san pham nao|sp nao|mon nao|tho nao|nhan vien nao"
                 r"|nha cung cap nao|ncc nao|mon gi|hang gi|nhung ai|nhung khach|nhung mat hang|nhung mon)\b")  # fmt: skip
TOP_K = re.compile(r"\btop\s*(\d{1,2})\b|\b(\d{1,2})\s+(?:mat hang|hang|san pham|sp|mon|khach|nguoi|nhan vien|tho"
                   r"|nha cung cap|ncc|loai|nhom|dong may|dong xe)\b")  # fmt: skip


def vocabulary(shop):
    """Every text the shop's data holds, as key(): a name the program returns must be one of them."""
    if getattr(shop, "_vocab", None) is None:
        words = set()
        frames = [t.df for t in shop.tables.values()] + [getattr(shop, n, None) for n in ("lines", "orders", "products")]
        for df in frames:
            if df is None:
                continue
            for c in df.columns:
                if df[c].dtype == object:
                    for v in df[c].dropna().unique():
                        if isinstance(v, str):
                            words |= {key(v), key(TAIL.sub(" ", v))}  # "Cao Long Minh (0321806537)" names Cao Long Minh
        shop._vocab = words
    return shop._vocab


def _names(answer):
    a = answer.get("answer")
    if answer.get("type") == "string":
        return [a]
    if answer.get("type") == "list":
        return [x for x in a if isinstance(x, str)]
    if answer.get("type") == "table" and a.get("rows"):
        return [r[0] for r in a["rows"] if r and isinstance(r[0], str)]
    return []


def result_problems(answer, question, m):
    """Why a result is almost surely wrong, or [] (answers of type abstain/unanswerable are not checked here)."""
    t, a, q = answer.get("type"), answer.get("answer"), fold(question)
    num = isinstance(a, (int, float)) and not isinstance(a, bool)
    if t in ("number", "percent") and num and a == 0 and not UNSOLD.search(q) and not (t == "percent" and CHANGE.search(q)):
        return [ZERO]
    if t == "percent" and num and not CHANGE.search(q):
        if a < 0:
            return ["a share cannot be negative: m.pct_change compares two periods; a share is part / whole * 100"]
        if a > 100 and SHARE.search(q):
            return [f"a share of a whole cannot be {a}%: divide the part by the whole it belongs to"]
    if WHO.search(q):
        vocab = vocabulary(m.shop)
        unknown = [x for x in _names(answer) if key(x) not in vocab]
        if unknown:
            return [f"{unknown[:3]} is not a name in the shop's data: return names exactly as the data writes them"]
    if (k := TOP_K.search(q)) and t in ("list", "table"):
        n = int(k[1] or k[2])
        got = len(a) if t == "list" else len(a.get("rows", []))
        if n > 1 and 0 < got != n and got < n:
            return [f"the question asks for {n} items, the program returned {got}"]
    if t == "date" and isinstance(a, str):
        periods = [p for _, p, _ in asked(question, m.P)]
        if len(periods) == 1:
            lo, hi = periods[0]
            day = a if len(a) == 10 else a + "-01"
            try:
                d = dt.date.fromisoformat(day)
            except ValueError:
                d = None
            if d and not (lo.replace(day=1) <= d <= hi):
                return [f"the date {a} lies outside the period the question asks about ({lo}, {hi})"]
    return []


SOFT = ("the question names", "the rulebook row")  # doubts the model may overrule by sending the same program again
# Proper names only: a group or channel name is often a plain noun ("máy cũ" in "tiền thu máy cũ") and misleads a retry
ENTITY_COLS = ("prod_name", "cust_name", "staff", "supplier")
NEGATED = re.compile(r"\b(?:khong|ko|k|chua)\s+(?:ke|tinh|gom|lay)\s+(?:\w+\s+){0,2}$|\b(?:tru|bo|ngoai)\s+(?:\w+\s+){0,1}$")


def named_values(shop, question):
    """Values of the shop's data the question names word for word (longest match), skipping "không kể X"."""
    if getattr(shop, "_values", None) is None:
        frames = [f for f in (shop.lines, shop.orders, shop.products) if f is not None]
        shop._values = {c: {v for f in frames if c in f for v in f[c].dropna().unique()
                            if isinstance(v, str) and len(key(v)) >= 4 and not key(v).startswith("khach le")}
                        for c in ENTITY_COLS}  # fmt: skip
    kq, out = f" {key(question)} ", []
    for col, vals in shop._values.items():
        hits = [v for v in vals if f" {key(v)} " in kq]
        for v in hits:
            if any(key(v) != key(w) and key(v) in key(w) for w in hits):
                continue
            before = kq[: kq.index(f" {key(v)} ") + 1]
            if not NEGATED.search(before):
                out.append((col, v))
    return out


def unused_names(question, code, shop):
    """Names the question gives that the program never mentions (it answers about something else)."""
    try:
        consts = [key(n.value) for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    except SyntaxError:
        return []
    out = []
    for col, v in named_values(shop, question):
        kv = key(v)
        if not any(kv in c or (len(c) >= 3 and c in kv) for c in consts):
            out.append(f"{SOFT[0]} {col} '{v}', but the program never uses it. If the program is right as it is, reply "
                       "with the same program")  # fmt: skip
    return out


def after_run(answer, question, m, used, code=""):
    """All checks on a program that ran -> problems, or []."""
    if answer.get("type") == "abstain":
        return []
    out = period_problems(question, m.P, used)
    if answer.get("type") != "unanswerable":
        out += result_problems(answer, question, m)
        out += unused_names(question, code, m.shop) if code else []
    if ZERO in out and code:
        out[out.index(ZERO)] = ZERO + values_seen(code, m.shop)
    return out


def values_seen(code, shop, k=25):
    """The commonest values of every text column the program names, so a retry filters on words the data uses."""
    frames = [t.df for t in shop.tables.values()] + [f for f in (shop.lines, shop.orders, shop.products) if f is not None]
    named = {n.value for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    parts = []
    for col in sorted(named):
        for f in frames:
            if col in f.columns and f[col].dtype == object:
                top = f[col].dropna().astype(str).str.slice(0, 40).value_counts().index[:k]
                parts.append(f"'{col}' holds e.g. {list(top)}")
                break
    return (". In the data, " + "; ".join(parts)) if parts else ""


# Rulebook rows a filter of m or the data layer already implements (no method names them in IMPLEMENTS).
COVERED = {"size m, size l", "thuoc ke don", "doanh thu theo hoat chat", "topping ghi chung o voi mon", "so sua, size bim"}
PLAIN_CALLS = {"len", "int", "float", "round", "sum", "max", "min", "sorted", "abs", "list", "half_up", "str"}
ACCESSORS = {"sold", "items", "orders", "rows", "with_order", "order_values", "daily"}  # rows to compute on, not a rule


def helper_calls(code):
    """The m methods a program answers with when it does nothing else (no pandas of its own), else None."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    used = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("m", "P"):
                used += [f.attr] if f.value.id == "m" else []
            elif not (isinstance(f, ast.Name) and f.id in PLAIN_CALLS):
                return None
        elif isinstance(n, ast.Subscript) and not (isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, int)):
            return None
    return None if not used or set(used) & ACCESSORS else used


def implemented(rule_key):
    from src.metrics import IMPLEMENTS

    k = fold(rule_key)
    return k in COVERED or any(k in fold(v) or fold(v) in k for v in IMPLEMENTS.values())


LINE_STOP = {"cua", "hang", "tiem", "quan", "shop", "va", "nha", "ban"}


def lines_of_business(rulebook, shop):
    """The rulebook's line-of-business subsections ("6. Theo ngành / Sửa xe") this shop belongs to: every word of the
    subsection's name occurs in the README's opening paragraph ("Sổ sửa xe chủ tiệm tự ghi...", "Cửa hàng điện thoại
    gửi..."); words met further down ("đơn qua điện thoại", "thuốc lá") say nothing about the shop's trade."""
    if getattr(shop, "_lines_of_business", None) is None:
        intro = re.split(r"\n\s*##", re.sub(r"^\s*#[^\n]*\n", "", shop.readme.get("text", "")), maxsplit=1)[0]
        text = set(re.findall(r"[a-z0-9]+", fold(intro)))
        subs = {r["section"] for r in rulebook.rules if r["section"].startswith("6") and " / " in r["section"]}
        shop._lines_of_business = {
            sec for sec in subs if (w := set(re.findall(r"[a-z0-9]+", fold(sec.split(" / ", 1)[1]))) - LINE_STOP) and w <= text}
    return shop._lines_of_business


def rule_problems(question, code, rulebook, shop, margin=1.0):
    """A rule of the shop's own line of business governs the question, no method implements it, and the program only
    calls methods written for other rules: the commonest confident error on a new line of business."""
    used = helper_calls(code)
    scored = rulebook.scored(question) if used else []
    if not scored:
        return []
    s, rule = scored[0]
    if rule["section"] not in lines_of_business(rulebook, shop) or not rule["key"] or implemented(rule["key"]):
        return []
    if len(scored) > 1 and scored[1][0] * margin > s and implemented(scored[1][1]["key"] or "-"):
        return []
    from src.metrics import IMPLEMENTS

    what = "; ".join(f"m.{fn} implements \"{IMPLEMENTS[fn]}\"" for fn in dict.fromkeys(used) if fn in IMPLEMENTS)
    return [f"{SOFT[1]} \"{rule['key']}\" ({rule['section']}): {rule['text']}. No method of m implements that rule"
            + (f" ({what})" if what else "") + ". If the rule changes which rows count or how (which tickets, which "
            "denominator, which names), compute it yourself from the rule's words on m.orders(p, status=None), "
            "m.sold(p) or T. If the methods already give exactly what the rule says, reply with the same program"]  # fmt: skip

