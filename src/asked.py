"""The periods a question names, read by rules, to check the periods a program used (rulebook §2).

Only phrases that name one period without doubt are read; anything else is left unchecked, so a question this
module does not understand is never blocked. Measured on the train bank: a rule stays only if it never rejects a
verified program.
"""
import datetime as dt
import re

from src.parse import fold

DAY = dt.timedelta(days=1)
YY = r"(20\d\d|2[3-7])(?![\d/.-])"
FULL_DATE = re.compile(r"(?<![\d/])(\d{1,2})[/.-](\d{1,2})[/.-](20\d\d)(?![\d/])")
MONTH = re.compile(r"(?<![\d/.-])(?<!quy )(?<!quy)(?:\b(?:thang|th|t)\s*\.?\s*|\b)(\d{1,2})\s*(?:/|-|\s+nam\s+)\s*" + YY)
QUARTER = re.compile(r"\b(?:quy|q)\s*(\d)\s*(?:/|-|\s+nam\s+|\s+)\s*" + YY)
YEAR = re.compile(r"\bnam\s+(20\d\d)\b")
LAST_DAYS = re.compile(r"\b(\d{1,3})\s+ngay\s+(?:gan\s+(?:nhat|day)|qua|vua\s+qua|vua\s+roi)\b")
RELATIVE = [  # (pattern, Periods method); only phrases that cannot mean anything else
    (re.compile(r"\bhom\s*qua\b|\bhqua\b"), "yesterday"),
    (re.compile(r"\btuan\s+(?:truoc|roi)\b"), "last_week"),
    (re.compile(r"\btuan\s+(?:nay|ni)\b"), "this_week"),
    (re.compile(r"\b(?:thang|th|t)\s+(?:nay|ni|hien tai)\b"), "this_month"),
    (re.compile(r"\b(?:hom\s*nay|hnay)\b"), "today"),
]
# "tới hết hôm nay", "tính đến hôm nay", "hôm nay là ...": the end of a period or the date of the question, not a period
UNTIL_TODAY = re.compile(r"\b(?:den|toi|tinh|qua|truoc|sau|tu|luon|ca|het)\s+(?:het\s+|ca\s+)?(?:ngay\s+)?(?:hom\s*nay|hnay)\b"
                         r"|\b(?:hom\s*nay|hnay)\s+la\b")  # fmt: skip
# "đến hết hôm nay", "tới hôm nay", "hết ngày hôm nay": a running total or a balance, not one day
TO_TODAY = re.compile(r"\b(?:den|toi|het|tinh den|tinh toi)\s+(?:het\s+)?(?:ngay\s+)?(?:hom\s*nay|hnay)\b")
LAST_MONTH = re.compile(r"\b(?:thang|th)\s+(?:truoc|roi)\b")
THIS_YEAR = re.compile(r"\bnam\s+(?:nay|ni)\b")


def _year(y):
    return int(y) + 2000 if len(y) == 2 else int(y)


def asked(question, P):
    """-> [(label, (start, end), how)]; how is "exact" (the program must use this very period) or "within"
    (every period the program uses must lie inside it)."""
    q = fold(question)
    if re.search(r"\btet\b|am lich", q):  # lunar periods have their own rules
        return []
    out = []
    dates = FULL_DATE.findall(q)
    q_wo = FULL_DATE.sub(" ", q)
    for d, mo, y in dates:
        try:
            dt.date(int(y), int(mo), int(d))
        except ValueError:
            return []  # not a date as we read it: do not check this question at all
    for mo, y in MONTH.findall(q_wo):
        if 1 <= int(mo) <= 12:
            out.append((f"tháng {int(mo)}/{_year(y)}", P.month(_year(y), int(mo)), "exact"))
    for n, y in QUARTER.findall(q_wo):
        if 1 <= int(n) <= 4:
            out.append((f"quý {n}/{_year(y)}", P.quarter(_year(y), int(n)), "exact"))
    for n in LAST_DAYS.findall(q):
        out.append((f"{n} ngày gần nhất", P.last_days(int(n)), "exact"))
    rel = UNTIL_TODAY.sub(" ", q)
    for rx, fn in RELATIVE:
        if rx.search(rel):
            out.append((fn, getattr(P, fn)(), "exact"))
    if LAST_MONTH.search(q) and not out and not re.search(r"\bso\b|cung ky|lien truoc", q):
        out.append(("last_month", P.last_month(), "exact"))
    if THIS_YEAR.search(q) and not out:
        out.append(("năm nay", P.year(P.as_of.year), "within"))
    if not out:
        years = {int(y) for y in YEAR.findall(q_wo)}
        if len(years) == 1 and not re.search(r"cung ky|nam truoc|nam ngoai", q):
            y = years.pop()
            out.append((f"năm {y}", P.year(y), "within"))
    return out


def period_problems(question, P, used):
    """Problems with the periods a program used (`used`: (start, end) pairs it built with P), or []."""
    used = [(a, b) for a, b in used if isinstance(a, dt.date) and isinstance(b, dt.date)]
    if not used:
        return []  # the program built its dates by hand: nothing to compare
    def seen(p):  # records after "hôm nay" do not exist: tháng này and the whole current month are the same rows
        return p[0], min(p[1], P.as_of)

    have, out = {seen(u) for u in used}, []
    named = asked(question, P)
    today = (P.as_of, P.as_of)
    if not named and TO_TODAY.search(fold(question)) and set(used) == {today}:
        out.append("the question asks about everything up to the end of today (a balance, a stock, what is still owed), "
                   "not about today alone: do not keep only today's rows")  # fmt: skip
    for label, p, how in named:
        if how == "exact" and seen(p) not in have:
            out.append(f"the question asks about {label} = ({p[0]}, {p[1]}), but the program uses "
                       f"{', '.join(f'({a}, {b})' for a, b in sorted(set(used)))}")  # fmt: skip
        if how == "within" and not any(p[0] <= a and b <= p[1] for a, b in used):
            out.append(f"the question asks about {label}, but no period of the program lies in it")
    return out


class Recorder:
    """Stands in for P in a program: same methods, and remembers every period it hands out."""

    def __init__(self, P):
        self._P, self.used = P, []

    def __getattr__(self, name):
        f = getattr(self._P, name)
        if not callable(f):
            return f

        def call(*a, **k):
            r = f(*a, **k)
            if isinstance(r, tuple) and len(r) == 2:
                self.used.append(r)
            return r

        return call
