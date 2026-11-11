"""Deterministic loader: every file of one shop -> typed tables. No LLM, no shop-specific logic.

    tables = load_shop("data/train/train/data/biz_105")   # {name: Table}
    tables["SoBanHang"].df                                 # typed DataFrame (+ _file, _sheet, _row)

What it does: finds the header row under report titles / group-header rows, stacks sheets and files that share
a header (monthly sheets, _1/_2 splits), and types each column as date / number / text from its values.
It does NOT forward-fill blank cells or drop duplicate rows: both depend on how rows group into orders,
which the schema layer decides.

Report: python -m src.load <split_dir or shop_dir> [more...]
"""
import csv
import datetime as dt
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field

import pandas as pd
from python_calamine import CalamineWorkbook

from src.parse import fold, is_serial, parse_date, parse_number, parse_qty

MIN_RATE = 0.98  # share of non-empty cells that must parse for a column to be typed
MAX_UNITS = 30  # a quantity column ("5 chai") has few distinct units; free text starting with a digit has many
META = ["_file", "_sheet", "_row"]


@dataclass
class Table:
    name: str
    df: pd.DataFrame  # typed; text columns hold str/None
    raw: pd.DataFrame  # same shape, every cell as cleaned text
    kinds: dict  # column -> date | datetime | number | text
    parts: list  # [(file, sheet, n_rows)]
    titles: list = field(default_factory=list)  # report lines found above the header
    bad: dict = field(default_factory=dict)  # column -> sample of cells that failed to parse
    empty: list = field(default_factory=list)  # all-blank columns, dropped from df
    dropped: list = field(default_factory=list)  # [(file, sheet, row, text)] subtotal/note rows left out


def cell(v):
    """Native Excel/SQLite value -> clean text ('' for blank). 45717.0 -> '45717'."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v == int(v) and abs(v) < 1e15 else repr(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    return " ".join(unicodedata.normalize("NFC", str(v)).split())


def find_header(rows, scan=30):
    """Index of the header row: the first row that fills most of the sheet's width with labels.

    Report titles and merged group headers ("Hàng | | | Tiền | |") fill only a few cells, so they are skipped.
    """
    head = rows[:scan]
    width = max((sum(1 for c in r if c) for r in head), default=0)
    for i, r in enumerate(head):
        filled = [c for c in r if c]
        if filled and len(filled) >= 0.7 * width and not any(
            parse_number(c) is not None or parse_date(c) for c in filled
        ):
            return i
    return None


_FOOT = re.compile(r"(?:tong|cong|total|subtotal|sum|luy ke)(?:\s|$)|(?:ghi chu|luu y|chu thich|note)\s*:")


def is_footer(row, width):
    """Subtotal and note lines typed into the sheet: 'Tổng ngày 01/03 | 3,411,000', 'Cộng', 'Ghi chú: ...'.

    They fill at most a couple of cells, unlike data rows. Counting them would double the revenue.
    """
    filled = [c for c in row if c]
    return len(filled) <= max(2, 0.3 * width) and any(_FOOT.match(fold(c)) for c in filled)


def _year(*names):
    for n in names:
        if m := re.search(r"(?<!\d)(20\d\d)(?!\d)", n):
            return int(m[1])
    return None


def _sheets(path):
    """Yield (sheet_name, rows) with every cell as clean text."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            yield "", [[cell(c) for c in r] for r in csv.reader(f)]
    elif ext in (".xlsx", ".xlsm", ".xls"):
        wb = CalamineWorkbook.from_path(path)
        for n in wb.sheet_names:
            yield n, [[cell(c) for c in r] for r in wb.get_sheet_by_name(n).to_python()]
    elif ext in (".sqlite", ".db", ".sqlite3"):
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)  # /data is read-only
        try:
            for (n,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
                cur = con.execute(f'select * from "{n}"')
                yield n, [[d[0] for d in cur.description]] + [[cell(c) for c in r] for r in cur]
        finally:
            con.close()


def _parts(shop_dir):
    """One part per sheet: dict(file, sheet, header, rows, titles, year)."""
    for f in sorted(os.listdir(shop_dir)):
        for sheet, rows in _sheets(os.path.join(shop_dir, f)):
            h = find_header(rows)
            if h is None:
                continue
            n = max(len(r) for r in rows[h:])
            header, seen = [], {}
            for i, c in enumerate(rows[h] + [""] * (n - len(rows[h]))):
                c = c or f"col_{i + 1}"
                seen[c] = seen.get(c, 0) + 1
                header.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
            body, dropped = [], []
            for i, r in enumerate(rows[h + 1 :], start=h + 2):  # 1-based sheet row number
                if not any(r) or r[: len(rows[h])] == rows[h]:  # blank row or repeated header
                    continue
                if is_footer(r, n):
                    dropped.append((f, sheet, i, " | ".join(c for c in r if c)))
                else:
                    body.append((i, r + [""] * (n - len(r))))
            yield {
                "file": f, "sheet": sheet, "header": header, "rows": body, "dropped": dropped, "year": _year(sheet, f),
                "titles": [" | ".join(c for c in r if c) for r in rows[:h] if any(r)],
            }


def _lcp(names):
    a, b = min(names), max(names)
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[:i]


def _name(parts):
    stems = [os.path.splitext(p["file"])[0] for p in parts]
    stem = stems[0]
    if len(set(stems)) > 1:  # SoBanHang_1 + SoBanHang_2026, BanHang_T10_2025 + BanHang_T1_2026
        cut = re.sub(r"(?:[\s_.\-]+(?:thang|tháng|th|t)?[\s_.\-]*)?\d*$", "", _lcp(stems), flags=re.IGNORECASE)
        stem = cut or stem
    sheets = {p["sheet"] for p in parts}
    one = next(iter(sheets)) if len(sheets) == 1 else ""
    return stem + (f".{one}" if one and not re.fullmatch(r"sheet\d*", one, re.IGNORECASE) else "")


def _is_datey(name):
    return bool(re.search(r"ngay|date|thoi gian|time|gio|han\b", fold(name)))


def infer(s, years, name):
    """Type one text column. -> (kind, typed Series, sample of unparsed cells)."""
    filled = s[s != ""]
    if filled.empty:
        return "empty", None, []
    vc = filled.value_counts()
    n = vc.sum()

    def share(ok):
        return sum(c for v, c in vc.items() if ok(v)) / n

    # Phone numbers, zero-padded codes, IMEI/barcodes are identifiers: keep them as text.
    is_id = any(re.fullmatch(r"0\d+", v) for v in vc.index) or all(v.isdigit() and len(v) >= 10 for v in vc.index)
    if not is_id:
        # Excel serials alone look like plain integers, so they need a real date beside them or a date-like name.
        explicit = share(lambda v: parse_date(v, year=2000, serial=False) is not None)
        if (explicit > 0 or _is_datey(name)) and explicit + share(is_serial) >= MIN_RATE:
            pairs = pd.DataFrame({"v": s, "y": years}).drop_duplicates()
            memo = {(v, y): parse_date(v, year=y) if v else None for v, y in zip(pairs.v, pairs.y)}
            out = pd.to_datetime(pd.Series([memo[k] for k in zip(s, years)], index=s.index))
            kind = "datetime" if (out.dropna().dt.normalize() != out.dropna()).any() else "date"
            return kind, out, [v for v in vc.index if parse_date(v, year=2000) is None][:5]

        memo, units = {}, set()
        for v in vc.index:
            memo[v] = parse_number(v)
            if memo[v] is None and (q := parse_qty(v)):
                memo[v] = q[0]
                units.add(q[1])
        # "264 Lương Định Của" also reads as number + unit: street names are many, or capitalised. Units are neither.
        if len(units) > MAX_UNITS or sum(u[0].isupper() for u in units) > len(units) / 2:
            memo = dict.fromkeys(memo)
        if share(lambda v: memo[v] is not None) >= MIN_RATE:
            return "number", s.map(memo).astype("float64"), [v for v in vc.index if memo[v] is None][:5]
    return "text", s.where(s != "", None), []


def _table(name, parts):
    cols = parts[0]["header"]
    recs, years = [], []
    for p in parts:
        order = [p["header"].index(c) for c in cols]  # same columns, maybe in another order
        for i, r in p["rows"]:
            recs.append([r[j] for j in order] + [p["file"], p["sheet"], i])
            years.append(p["year"])
    raw = pd.DataFrame(recs, columns=cols + META)
    years = pd.Series(years, dtype="object")
    t = Table(name, raw[META].copy(), raw, {}, [(p["file"], p["sheet"], len(p["rows"])) for p in parts])
    t.titles = list(dict.fromkeys(x for p in parts for x in p["titles"]))
    t.dropped = [d for p in parts for d in p["dropped"]]
    typed = {}
    for c in cols:
        kind, col, bad = infer(raw[c], years, c)
        if kind == "empty":
            t.empty.append(c)
            continue
        typed[c], t.kinds[c] = col, kind
        if bad:
            t.bad[c] = bad
    t.df = pd.concat([pd.DataFrame(typed), raw[META]], axis=1)
    return t


def load_shop(shop_dir):
    """-> {table name: Table}. Sheets/files with the same set of column names become one table."""
    groups = {}
    for p in _parts(shop_dir):
        groups.setdefault(tuple(sorted(p["header"])), []).append(p)
    tables = {}
    for parts in groups.values():
        name, k = _name(parts), 1
        while name in tables:  # two different tables with the same derived name
            k += 1
            name = f"{_name(parts)}_{k}"
        tables[name] = _table(name, parts)
    return tables


def report(shop_dir):
    lines = [f"===== {os.path.basename(shop_dir)}"]
    for t in load_shop(shop_dir).values():
        files = sorted({f for f, _, _ in t.parts})
        lines.append(f"  [{t.name}] rows={len(t.df)} parts={len(t.parts)} files={files[:3]}{'...' if len(files) > 3 else ''}")
        for c, k in t.kinds.items():
            s = t.df[c]
            ex = [str(v)[:24] for v in t.raw[c][t.raw[c] != ""].drop_duplicates().head(3)]
            bad = f"  BAD {t.bad[c]}" if c in t.bad else ""
            lines.append(f"      {k:8} null={s.isna().mean():5.1%}  {c[:28]:28} {ex}{bad}")
        if t.empty:
            lines.append(f"      empty: {t.empty}")
        if t.dropped:
            kinds = sorted({re.sub(r"\d", "#", d[3])[:40] for d in t.dropped})
            lines.append(f"      dropped {len(t.dropped)} subtotal/note rows: {kinds[:6]}")
    return "\n".join(lines)


if __name__ == "__main__":
    for root in sys.argv[1:]:
        data = os.path.join(root, "data")
        shops = sorted(os.path.join(data, d) for d in os.listdir(data)) if os.path.isdir(data) else [root]
        for shop in shops:
            print(report(shop), flush=True)
