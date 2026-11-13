"""Side books (sổ phụ) made usable for any trade: entry rows filled in, and links to the sales book found.

Books such as a debt or instalment ledger write an entry on its first row (date, customer, ...) and every later
payment on a row of its own with those cells blank. `entries` fills the blank text and date cells of such
continuation rows from their entry row and numbers the entries (`_entry`); numbers are never copied, so a sum
over a column stays a sum of what was written.

`links` finds columns of a side book that point at sales orders (an order number, or "STT 26" in a note) or at
values of a sales column (an IMEI), so a rule like "only orders not cancelled or returned" can be applied by a
join instead of guesswork.
"""
import re

import pandas as pd

META = ("_file", "_sheet", "_row")
REF = re.compile(r"(?:stt|so|ma|hd|don|#)\s*[:.#]?\s*([A-Za-z]*\d+)", re.IGNORECASE)


def _data_cols(df):
    return [c for c in df.columns if c not in META and not str(c).startswith("_")]


def entries(df):
    """-> (filled copy of df with `_entry`, number of continuation rows), or (df, 0) if it is not such a book."""
    cols = _data_cols(df)
    dates = [c for c in cols if pd.api.types.is_datetime64_any_dtype(df[c])]
    if not dates or len(df) < 5:
        return df, 0
    head = df[dates[0]].notna()
    filled = df[cols].notna().sum(axis=1)
    text = [c for c in cols if df[c].dtype == object]
    # A payment row leaves the entry's text (customer, phone, item) blank; a list row with one blank date does not.
    cont = ~head & (filled > 0) & (df[text].notna().sum(axis=1) <= len(text) / 2)
    if cont.sum() < 3 or not head.iloc[0]:
        return df, 0
    out = df.copy()
    out["_entry"] = head.cumsum()
    copy = [c for c in cols if not pd.api.types.is_numeric_dtype(df[c]) or c == dates[0]]
    for c in copy:
        out[c] = out[c].where(out[c].notna() | ~cont, out.groupby("_entry")[c].transform("first"))
    return out, int(cont.sum())


def norm_id(v):
    """An order number as text, whatever way it was written: 26, 26.0, '26', 'STT 26' -> '26'."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    if re.fullmatch(r"\d+(?:\.0+)?", s):
        return str(int(float(s)))
    m = REF.search(s)
    return m[1].lstrip("0") or "0" if m and m[1].isdigit() else (m[1] if m else s)


def _tokens(series):
    out = set()
    for v in series.dropna().astype(str):
        out.update(t.strip() for t in re.split(r"[/,;]", v) if len(t.strip()) >= 6)
    return out


def links(shop, views):
    """-> [(table, column, target)], target "order" (a sales order id) or ("lines", column) (a value of the sales book)."""
    found = []
    sales = shop.lines if shop.lines is not None else shop.orders
    if sales is None:
        return found
    ids = {norm_id(v) for v in sales["order_id"].dropna().unique()} if "order_id" in sales else set()
    keyed = [c for c in sales.columns if sales[c].dtype == object and sales[c].nunique() >= 50 and c not in ("cust_name", "prod_name", "note")]
    pools = {c: _tokens(sales[c]) for c in keyed}
    for name, df in views.items():
        spec = shop.manifest["tables"].get(name, {})
        if spec.get("role") in ("lines", "orders"):
            continue
        for c in _data_cols(df):
            vals = df[c].dropna()
            if vals.nunique() < 30:  # a day of the month, a count, a status: too few values to be a reference
                continue
            if ids:
                hit = vals.map(norm_id).isin(ids).mean()
                if hit >= 0.9 and (pd.api.types.is_numeric_dtype(vals) or vals.astype(str).str.contains(r"\d").all()):
                    found.append((name, c, "order"))
                    continue
            if vals.dtype == object and spec.get("role") != "products":
                toks = set(vals.astype(str).str.strip())
                for sc, pool in pools.items():
                    if len(toks) >= 10 and len(toks & pool) / len(toks) >= 0.9:
                        found.append((name, c, ("lines", sc)))
                        break
    return found
