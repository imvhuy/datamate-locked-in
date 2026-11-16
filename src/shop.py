"""Canonical views of one shop, built from the loaded tables and a manifest.

    shop = open_shop("data/train/train/data/biz_105")
    shop.lines    # one row per sales line: canonical field names + o_status, o_date, o_ts, cust, prod
    shop.orders   # one row per order (None when the shop keeps no order ids)

Rulebook §8 lives here: blank cells inherit the order's first row, identical rows of one order count once,
every order has one of four statuses.
"""
import json
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from src.ledger import entries, links
from src.load import META, load_shop
from src.parse import fold
from src.schema import guess_manifest, read_readme

# First match wins: "khách huỷ" is a cancel, "đã trả hàng" a return, "đang giao" still counts as a sale.
STATUS = [
    ("cancel", r"\bhuy\b|cancel|khong giao duoc"),  # \b: "đang vận chuyển" contains "huy"
    ("return", r"\btra\b|hoan hang|return"),
    ("shipping", r"\bdang\b|cho giao|shipping"),
    ("done", r"xong|hoan thanh|thanh cong|\bok\b|da lay|da giao|complete|done"),
]
NOTE_STATUS = [  # stricter than STATUS: free-text notes also say "nợ, đã trả đủ"
    ("cancel", r"\bhuy\b"),
    ("return", r"tra lai|tra hang|mang tra|doi tra|hoan hang"),
    ("shipping", r"dang giao|chua giao|cho giao"),
]
VALID = ("done", "shipping")  # orders that count as revenue (§1)
WALKIN = {"khach le", "le", "kl", "khach vang lai", "vang lai"}
# Rows of a sales book that are order-level money, not goods: §1 revenue neither takes off a shop voucher nor adds
# shipping, and they are not items. They stay in the lines for questions about vouchers, points and shipping.
CHARGE = re.compile(r"^(?:voucher|ma giam gia|phi (?:ship|giao hang|van chuyen)|dung \d+ diem)\b")
# A repair book names the vehicle ("Vision 110 51E5-585.28"); a vehicle is its licence plate.
PLATE = re.compile(r"\b(\d{2}[A-Z][A-Z0-9]?)[-\s.]?(\d{3}\.\d{2}|\d{4,5})\b")
PLATE_NOTE = "customers are vehicles, identified by licence plate"


def cust_key(text):
    """Customer identity from a name: 'Nam - 0977 824 094' == 'Nam (0977824094)' (same letters, same number)."""
    return re.sub(r"(?<=\d) (?=\d)", "", key(text))


def is_charge(name):
    return isinstance(name, str) and bool(CHARGE.match(key(name)))


def plate_of(text):
    """'Vision 110 51e5-585.28' -> '51E5-585.28'; None when the text holds no plate."""
    m = PLATE.search(str(text).upper()) if isinstance(text, str) else None
    if not m:
        return None
    num = m.group(2).replace(".", "")
    return f"{m.group(1)}-{num[:3]}.{num[3:]}" if len(num) == 5 else f"{m.group(1)}-{num}"
ORDER_FIELDS = [
    "date", "ts", "status", "note", "cust_name", "cust_id", "cust_group", "payment", "staff", "channel",
    "order_goods", "order_disc", "order_total", "ship", "paid", "debt",
]  # fmt: skip


PAY = [  # payment method written on the order (§1): COD is its own kind, never cash
    ("cod", r"cod|thu ho"),
    ("debt", r"\bno\b|debt"),
    ("cash", r"tien mat|cash|\btm\b"),
    ("transfer", r"chuyen khoan|transfer|\bck\b"),
    ("ewallet", r"vi dien tu|ewallet|momo|zalopay"),
]
CHANNEL = [
    ("zalo", r"zalo"),
    ("phone", r"phone|dien thoai"),
    ("ecom", r"\bsan\b|tmdt|shopee|lazada|tiktok"),
    ("store", r"store|quay|tai tiem|cua hang|tai cho"),
]


def label(text, rules):
    """First rule whose pattern occurs in the folded text, else None."""
    t = fold(text)
    return next((name for name, rx in rules if re.search(rx, t)), None)


def status_of(text):
    return label(text, STATUS)


def key(text):
    """Spelling-insensitive key for names: 'Lưu Hà An' == 'luu ha an' == 'Lưu  Hà-An'."""
    return re.sub(r"[^a-z0-9]+", " ", fold(text)).strip()


@dataclass
class Shop:
    tables: dict
    manifest: dict
    readme: dict
    lines: pd.DataFrame = None
    orders: pd.DataFrame = None
    products: pd.DataFrame = None  # catalogue: prod (identity used in lines), prod_name, group, base_unit...
    purchases: pd.DataFrame = None  # purchase lines with their slip's date/supplier/total; cancelled slips removed
    stock: pd.DataFrame = None  # prod, stock_qty at the last day of data
    payments: pd.DataFrame = None  # order_id, pay_ts, pay_amount: one row per payment
    factor: dict = field(default_factory=dict)  # (prod, key(big unit)) -> base units in one big unit
    by_id: bool = False  # products are identified by code (else by the key of their name)
    by_role: dict = field(default_factory=dict)  # role -> [canonical DataFrame]
    notes: list = field(default_factory=list)  # things a caller should doubt
    views: dict = field(default_factory=dict)  # table name -> what programs see as T[name] (side books filled in)
    filled: dict = field(default_factory=dict)  # table name -> continuation rows filled from their entry row
    links: list = field(default_factory=list)  # (table, column, "order" or ("lines", column)) found by src.ledger

    def find_prod(self, text):
        """Product code or any spelling of its name -> the identity used in lines['prod'] (None if unknown)."""
        if self.products is None or not isinstance(text, str):
            return None
        cat, k = self.products, key(text)
        hit = cat[(cat["prod"].map(key) == k) | (cat["pkey"] == k)]
        return hit["prod"].iloc[0] if len(hit) else None

    def ident(self, df):
        """Product identity of each row of any table, the way sales lines identify products."""
        cat = self.products
        if self.by_id:
            if "prod_id" in df:
                return df["prod_id"]
            ids = dict(zip(cat["pkey"], cat["prod"])) if cat is not None else {}
            return df["prod_name"].map(lambda n: ids.get(key(n)), na_action="ignore")
        if "prod_name" in df:
            return df["prod_name"].map(key, na_action="ignore")
        ids = dict(zip(cat["prod_id"], cat["prod"])) if cat is not None and "prod_id" in cat else {}
        return df["prod_id"].map(lambda c: ids.get(c, c), na_action="ignore")

    def name_of(self, prod):
        """Display name of a product identity, as written in the shop's product list."""
        cat = self.products
        hit = cat.loc[cat["prod"] == prod, "prod_name"] if cat is not None and "prod_name" in cat else []
        return hit.iloc[0] if len(hit) else prod


def _canon(table, spec):
    df = table.df.rename(columns=spec["cols"])
    return df.loc[:, ~df.columns.duplicated()].copy()


def _biggest(frames):
    return max(frames, key=len) if frames else None


def _worst(statuses):
    s = set(statuses)
    return next((x for x in ("cancel", "return", "shipping", "done") if x in s), None)


def _sales(lines, orders, notes):
    """-> (lines, orders) in canonical form."""
    if lines is not None:
        data = [c for c in lines.columns if c not in META]
        if "order_id" in lines:
            lines["order_id"] = lines["order_id"].ffill()  # blank on later rows of the same order
            g = lines.groupby("order_id", sort=False)
            for f in ORDER_FIELDS:
                if f in lines and lines[f].isna().any():
                    lines[f] = lines[f].fillna(g[f].transform("first"))
            lines = lines.drop_duplicates(subset=data)  # export glitch: same row twice in one order
        else:
            notes.append("no order id: every row is its own sale, order counts are not available")
    if orders is not None:
        orders = orders.drop_duplicates(subset=[c for c in orders.columns if c not in META])
        if orders["order_id"].duplicated().any():
            notes.append("orders table has several different rows for one order id")
            orders = orders.drop_duplicates(subset="order_id")
        if lines is not None and "order_id" in lines:
            extra = ["order_id"] + [c for c in orders.columns if c not in lines.columns and c not in META]
            lines = lines.merge(orders[extra], on="order_id", how="left")

    src = lines if lines is not None else orders
    if src is None:
        return None, None
    if "date" not in src and "ts" not in src:
        notes.append("the sales table has no date column that could be recognised: sales are unusable")
        return None, None
    src = src.copy()  # drop_duplicates hands back a slice
    if "date" not in src and "ts" in src:
        src["date"] = src["ts"].dt.normalize()
    if "status" in src:
        memo = {v: status_of(v) for v in src["status"].dropna().unique()}
        src["st"] = src["status"].map(memo)
        unknown = sorted(v for v, s in memo.items() if s is None)
        if unknown:
            notes.append(f"unrecognised status values: {unknown[:8]}")
    elif "note" in src:  # §8: a book without a status column writes it in the note; no mention = done
        memo = {v: label(v, NOTE_STATUS) or "done" for v in src["note"].dropna().unique()}
        src["st"] = src["note"].map(memo).fillna("done")
        notes.append("no status column: status read from the note column")
    else:
        src["st"] = "done"
        notes.append("no status column: every sale treated as done")
    if "cust_id" not in src and "cust_name" not in src:  # a column left unmapped that holds licence plates names the customer
        for c in [c for c in src.columns if not re.fullmatch(r"[a-z_]+", str(c)) and src[c].dtype == object]:
            named = src[c].dropna()
            if len(named) > len(src) / 2 and named.map(plate_of).notna().mean() > 0.8:
                src["cust_name"] = src[c]
                break
    for name, col in (("cust", "cust_id"), ("prod", "prod_id")):  # identity: the code when there is one, else the name
        alt = col.replace("_id", "_name")
        if col in src:
            src[name] = src[col]
        elif alt in src:  # no such column at all: leave it out, so asking about it is "unanswerable"
            src[name] = src[alt].map(key if name == "prod" else cust_key, na_action="ignore")
    if "cust_name" in src:
        src.loc[src["cust_name"].map(lambda v: isinstance(v, str) and key(v) in WALKIN), "cust"] = None
        named = src["cust_name"].dropna()
        plates = named.map(plate_of)
        if len(named) and plates.notna().mean() > 0.8:
            src["cust"] = src["cust_name"].map(plate_of)
            notes.append(PLATE_NOTE)

    if "order_id" in src:
        g = src.groupby("order_id", sort=False)
        src["o_status"] = g["st"].transform(_worst)
        src["o_date"] = g["date"].transform("min")  # a return row carries the later return date
        if "ts" in src:
            src["o_ts"] = g["ts"].transform("min")
    else:
        src["o_status"], src["o_date"] = src["st"], src["date"]
        if "ts" in src:
            src["o_ts"] = src["ts"]

    if lines is None:
        if "order_goods" not in src and "order_total" in src and not {"ship", "order_disc"} & set(src.columns):
            src["order_goods"] = src["order_total"]  # nothing added or taken off: the total is the goods
        return None, src
    if "order_id" not in src:
        return src, None
    first = [c for c in ["o_date", "o_ts", "o_status", "cust", *ORDER_FIELDS] if c in src]
    out = g[first].first()
    if "order_goods" not in out:
        sold = src[src["st"] != "return"]  # return rows repeat the goods with a minus sign
        if "prod_name" in sold:
            sold = sold[~sold["prod_name"].map(is_charge)]
        out["order_goods"] = sold.groupby("order_id")["amount"].sum().reindex(out.index).fillna(0)
    return src, out.reset_index()


def _matches(lines, cat):
    """How many sold products some column of a product list names: picks the real catalogue among look-alikes."""
    sold = {key(str(v)) for c in ("prod_id", "prod_name") if c in lines for v in lines[c].dropna().unique()}
    cols = [c for c in cat.columns if c not in META and cat[c].dtype == object]
    return max((len(sold & {key(str(v)) for v in cat[c].dropna().unique()}) for c in cols), default=0)


def _book_names(lines, cat):
    """A product list that says how the sales book abbreviates each product (sheet "Ghi tắt": "CL 9D" -> "Kính cường
    lực 9D"): the lines get the full name, and keep what the book wrote in `book_name`."""
    sold = set(lines["prod_name"].dropna().map(key))
    if not sold or len(sold & set(cat["prod_name"].dropna().map(key))) >= len(sold) / 2:
        return lines
    for c in cat.columns:
        if c in META or c == "prod_name" or cat[c].dtype != object:
            continue
        alias = cat[c].map(lambda v: key(v) if isinstance(v, str) else None)
        if len(sold & set(alias.dropna())) >= len(sold) / 2:
            full = {a: n for a, n in zip(alias, cat["prod_name"]) if a and isinstance(n, str)}
            lines = lines.assign(book_name=lines["prod_name"])
            lines["prod_name"] = [full.get(key(v), v) if isinstance(v, str) else v for v in lines["prod_name"]]
            lines["prod"] = lines["prod_name"].map(key, na_action="ignore")
            return lines
    return lines


TOPPING = re.compile(r"^(?P<dish>.+?)\s*\+\s*(?:(?P<n>\d+)\s+)?(?P<top>[^+()]+?)\s*\((?P<price>\d[\d.,]*)\s*(?:đ|d|vnd|vnđ)?\)\s*$", re.IGNORECASE)


def split_toppings(lines):
    """Rulebook "Topping ghi chung ô với món": 'Matcha latte (M) + 2 Thạch cà phê (14.000đ)' is two items sold. The
    line's quantity, price and unit are the dish's; the topping is its own line of n portions (1 when no number) for
    the money in brackets, which the line total already includes. It takes the name of the topping sold on separate
    lines ("Topping Thạch cà phê") when there is one."""
    if "prod_name" not in lines or "prod_id" in lines:
        return lines
    parts = lines["prod_name"].astype(str).str.extract(TOPPING)
    hit = parts["dish"].notna()
    if not hit.any():
        return lines
    known = {key(n): n for n in lines.loc[~hit, "prod_name"].dropna().unique()}

    def name(top):
        k = key(top)
        return known.get(f"topping {k}") or next((n for kk, n in known.items() if kk.endswith(k) and "topping" in kk), None) or known.get(k, top)

    dish, top = lines[hit].copy(), lines[hit].copy()
    money = parts.loc[hit, "price"].map(lambda v: float(re.sub(r"[.,]", "", v)))
    n = parts.loc[hit, "n"].fillna("1").astype(float)
    if "amount" in dish:
        dish["amount"] = dish["amount"] - money
        top["amount"] = money
    if "price" in top:
        top["price"] = money / n
    if "qty" in top:
        top["qty"] = n
    if "unit" in top:
        top["unit"] = None
    dish["prod_name"] = parts.loc[hit, "dish"].str.strip()
    top["prod_name"] = parts.loc[hit, "top"].map(name)
    out = pd.concat([lines[~hit], dish, top]).sort_index(kind="stable").reset_index(drop=True)
    out["prod"] = out["prod_name"].map(key, na_action="ignore")
    return out


def _toppings(shop):
    if shop.lines is not None:
        n = len(shop.lines)
        shop.lines = split_toppings(shop.lines)
        if len(shop.lines) > n:
            shop.notes.append(f"{len(shop.lines) - n} dish + topping cells split into two items")


def _group_column(cat):
    """A product list whose kind-of-goods column the schema left unnamed ("Loại": máy mới / máy cũ / phụ kiện)."""
    if "group" in cat:
        return cat
    for c in cat.columns:
        name = re.sub(r"[^a-z]", "", fold(str(c)))
        if c not in META and name in ("loai", "loaihang", "nhom", "phanloai") and cat[c].dtype == object and cat[c].nunique() <= 30:
            return cat.rename(columns={c: "group"})
    return cat


def _catalogue(shop):
    """Product list keyed the way sales lines identify products, and its attributes copied onto the lines."""
    lines = shop.lines
    if lines is None or "prod" not in lines or lines["prod"].isna().all():
        return
    # No product list: a stock sheet that names every product with its unit and cost is the catalogue.
    pool = shop.by_role.get("products") or [s.rename(columns={"cost": "prod_cost"}) for s in shop.by_role.get("stock", [])]
    cat = max(pool, key=lambda c: (_matches(lines, c), len(c)), default=None)
    if cat is not None and _matches(lines, cat) == 0:
        cat = None
    if cat is not None and "prod_id" not in lines and "prod_name" in cat and "prod_name" in lines:
        lines, cat = _book_names(lines, cat), _group_column(cat)
    idcol = "prod_id" if "prod_id" in lines else "prod_name"
    if cat is not None and idcol in cat:
        cat = cat.assign(prod=cat[idcol] if idcol == "prod_id" else cat[idcol].map(key, na_action="ignore"))
        # Sold but not listed ("Voucher", "Phí ship" lines): still products of the shop, by the name the book uses.
        cols = [c for c in ("prod", "prod_name") if c in lines]
        cat = pd.concat([cat, lines.loc[~lines["prod"].isin(cat["prod"]), cols].drop_duplicates("prod")], ignore_index=True)
    else:  # no product list: the products are whatever was sold
        cols = [c for c in ("prod_name", "group", "unit") if c in lines]
        cat = lines.groupby("prod", sort=False)[cols].first().reset_index()
    cat = cat.dropna(subset=["prod"]).drop_duplicates("prod").reset_index(drop=True)
    cat["pkey"] = cat["prod_name"].map(key, na_action="ignore") if "prod_name" in cat else None
    add = [c for c in ("prod_name", "group", "base_unit") if c in cat and c not in lines]
    # Catalogue columns no schema field names (size, brand, kind: "Số / Size", "Hãng"...) go onto the lines under
    # their own names, so a program can filter m.sold(p) on them: a new line of business mostly adds such attributes.
    add += [c for c in cat.columns if c not in lines and c not in add and c not in META and not re.fullmatch(r"[a-z_]+", str(c))
            and cat[c].dtype == object and 2 <= cat[c].nunique() <= 60
            and not cat[c].dropna().astype(str).str.fullmatch(r"\d{6,}").mean() > 0.5]  # fmt: skip  (barcodes are no attribute)
    if add:
        lines = lines.merge(cat[["prod", *add]], on="prod", how="left")
    shop.lines, shop.products, shop.by_id = lines, cat, idcol == "prod_id"


def _base_units(shop):
    """Quantity in the product's base unit (§10) on every sales line, and which unit that is."""
    lines = shop.lines
    if lines is None or "qty" not in lines:
        return
    factor = shop.factor  # a unit with no conversion row is the base unit (factor 1)
    for u in shop.by_role.get("units", []):
        if {"big_unit", "factor"} <= set(u.columns) and {"prod_id", "prod_name"} & set(u.columns):
            factor.update({(p, key(b)): f for p, b, f in zip(shop.ident(u), u["big_unit"], u["factor"]) if pd.notna(f)})
    if "base_qty" not in lines:
        units = lines["unit"] if "unit" in lines else [None] * len(lines)
        lines["base_qty"] = lines["qty"] * [
            factor.get((p, key(u)), 1) if isinstance(u, str) else 1 for p, u in zip(lines["prod"], units)
        ]
    if "base_unit" not in lines and "unit" in lines:
        same = lines[(lines["base_qty"] == lines["qty"]) & lines["unit"].notna()]
        lines["base_unit"] = lines["prod"].map(same.groupby("prod")["unit"].agg(lambda s: s.mode().iat[0]))


def _customers(shop):
    """Customer attributes (name, group) from the customer list onto lines and orders; flag non-goods rows."""
    cust = _biggest(shop.by_role.get("customers"))
    for attr in ("lines", "orders"):
        df = getattr(shop, attr)
        if df is None:
            continue
        if cust is not None and "cust_id" in cust and "cust_id" in df:
            add = [c for c in ("cust_name", "cust_group") if c in cust and c not in df]
            if add:
                df = df.merge(cust.drop_duplicates("cust_id")[["cust_id", *add]], on="cust_id", how="left")
        if attr == "lines" and "prod_name" in df:  # §3: the service-fee row is revenue but not an item
            memo = {n: key(n) != "phi phuc vu" for n in df["prod_name"].dropna().unique()}
            df["is_item"] = df["prod_name"].map(memo).fillna(True).astype(bool)
            charge = df["prod_name"].map(is_charge)
            if charge.any():
                df["charge"], df["is_item"] = charge, df["is_item"] & ~charge
        for col, raw, rules in (("pay", "payment", PAY), ("chan", "channel", CHANNEL)):
            if raw in df:  # one vocabulary whatever the shop writes: "Tiền mặt" / "cash" -> cash
                df[col] = df[raw].map({v: label(v, rules) for v in df[raw].dropna().unique()})
        if attr == "lines" and {"cost", "qty", "amount"} <= set(df.columns):
            df["profit"] = df["amount"] - df["qty"] * df["cost"]  # §5: cost is per selling unit of the line
        setattr(shop, attr, df)


HONORIFIC = re.compile(r"^\s*(?:anh|chị|chi|em|cô|co|chú|chu|a|c|e)\s*[.\s]\s*", re.IGNORECASE)


def _staff_names(shop):
    """A book that writes staff by what they are called ("A.Đức", "a Đức", "Đức") while the staff list holds full
    names ("Lương Chi Đức"): one person, written as in the list. A call name two staff share stays as written."""
    staff = next((t for t in shop.by_role.get("staff", []) if "staff_name" in t), None)
    if staff is None:
        return
    full = [n for n in staff["staff_name"].dropna().unique() if isinstance(n, str)]
    for attr in ("lines", "orders"):
        df = getattr(shop, attr)
        if df is None or "staff" not in df or (set(df["staff"].dropna()) & set(staff.get("staff", pd.Series()).dropna())):
            continue  # no staff column, or it holds the list's staff codes already
        memo = {}
        for v in df["staff"].dropna().unique():
            core = key(HONORIFIC.sub("", str(v)))
            hits = [n for n in full if key(n) == core or key(n).endswith(" " + core)]
            memo[v] = hits[0] if len(hits) == 1 else v
        df["staff"] = df["staff"].replace(memo)
        setattr(shop, attr, df)


NOTE_DEBT = re.compile(r"\bno\b|ghi so|thieu")
AMOUNT = r"([\d][\d.,]*)\s*(k\b|nghin|ngan|trieu|tr\b|d\b|dong)?"


def _money(num, unit):
    v = float(re.sub(r"[.,](?=\d{3}(?!\d))", "", num).replace(",", "."))
    return v * {"k": 1e3, "nghin": 1e3, "ngan": 1e3, "trieu": 1e6, "tr": 1e6}.get(unit or "", 1)


def note_debt(note, total):
    """What an order still owes, read from a free-text note ("còn thiếu 73 nghìn", "đưa trước 236k, còn lại ghi sổ",
    "nợ cả đơn", "nợ, đã trả đủ"). 0 when the note says nothing about debt or says it was paid off."""
    t = fold(note) if isinstance(note, str) else ""
    if not NOTE_DEBT.search(t):
        return 0.0
    if re.search(r"\b(du|het)\b", t) and not re.search(r"thieu|con lai", t):
        return 0.0
    owed = re.search(r"(?:con thieu|thieu lai|thieu|ghi no|ghi so|\bno\b)\s*:?\s*" + AMOUNT, t)
    if owed:
        return _money(owed[1], owed[2])
    paid = re.search(r"(?:dua truoc|tra truoc|\btra\b|\bck\b|chuyen khoan|dua)\s*:?\s*" + AMOUNT, t)
    if paid and "con lai" in t:
        return max(total - _money(paid[1], paid[2]), 0.0)
    return float(total)  # "nợ cả đơn", "ghi sổ", "chưa trả": the whole order


def _note_debt(shop):
    """README-described books that write the debt only in the order note: give the orders a `debt` column (the
    state at the last day of data, like a debt column would be)."""
    o = shop.orders
    if o is None or "debt" in o or "paid" in o or "note" not in o or "order_total" not in o:
        return
    if not o["note"].map(lambda v: isinstance(v, str) and bool(NOTE_DEBT.search(fold(v)))).any():
        return
    o["debt"] = [note_debt(n, tot) for n, tot in zip(o["note"], o["order_total"].fillna(0))]
    shop.notes.append("debt read from the order notes")


def _side_tables(shop):
    """Purchases, stock, payments and drug attributes, keyed like the sales lines."""
    role = shop.by_role
    frames = role.get("purchases", [])
    body = _biggest([f for f in frames if "po_qty" in f])
    head = _biggest([f for f in frames if "po_qty" not in f and "po_id" in f])
    if body is not None:
        if head is not None:  # slip header: date, supplier, status, total
            extra = [c for c in head.columns if c not in body.columns and c not in META]
            body = body.merge(head.drop_duplicates("po_id")[["po_id", *extra]], on="po_id", how="left")
        if "po_status" in body:  # §5: bỏ phiếu huỷ
            body = body[body["po_status"].map(status_of) != "cancel"].copy()
        body["prod"] = shop.ident(body)
        if "po_amount" not in body and "po_price" in body:
            body["po_amount"] = body["po_qty"] * body["po_price"]
        units = body["po_unit"] if "po_unit" in body else [None] * len(body)
        body["base_qty"] = body["po_qty"] * [
            shop.factor.get((p, key(u)), 1) if isinstance(u, str) else 1 for p, u in zip(body["prod"], units)
        ]
        names = _biggest(role.get("suppliers"))
        if "supplier" not in body and names is not None and {"supplier_id", "supplier"} <= set(names.columns):
            body = body.merge(names[["supplier_id", "supplier"]].drop_duplicates("supplier_id"), on="supplier_id", how="left")
        shop.purchases = body
    stock = _biggest(role.get("stock"))
    if stock is not None:
        shop.stock = stock.assign(prod=shop.ident(stock))
    shop.payments = _biggest(role.get("payments"))
    drugs = _biggest(role.get("drugs"))
    if drugs is not None and shop.lines is not None:
        drugs = drugs.assign(prod=shop.ident(drugs)).drop_duplicates("prod")
        add = [c for c in ("ingredient", "rx") if c in drugs]
        lines = shop.lines.merge(drugs[["prod", *add]], on="prod", how="left")
        if "rx" in lines:  # §6: anything not marked kê đơn is hàng không kê đơn
            lines["rx"] = lines["rx"].map(lambda v: str(v).strip().lower() in ("true", "1", "1.0", "x", "co", "có", "yes"))
        shop.lines = lines


def _ledgers(shop):
    """T as programs see it: side books with their continuation rows filled in, and their links to the sales book."""
    shop.views = {n: t.df for n, t in shop.tables.items()}
    for n, spec in shop.manifest["tables"].items():
        if spec.get("role") not in ("lines", "orders") and n in shop.views:
            shop.views[n], k = entries(shop.views[n])
            if k:
                shop.filled[n] = k
    shop.links = links(shop, shop.views)


# README: "mỗi dòng một lượt xe vào sửa (một phiếu)" -> a row is a whole sale; "mỗi dòng một mặt hàng" is not.
ROW_IS_ORDER = re.compile(r"moi dong (?:la )?mot (?:luot|phieu|don|hoa don)\b")


def setattr_sales(shop):
    role = shop.by_role
    dated = [[f for f in role.get(r, []) if {"date", "ts"} & set(f.columns)] or role.get(r) for r in ("lines", "orders")]
    lines, orders = _biggest(dated[0]), _biggest(dated[1])  # a sales table has dates
    one = ROW_IS_ORDER.search(fold(shop.readme.get("text", "")))
    for df in (lines, orders):  # every row is its own order: an orders table always, a book when the README says so
        if df is not None and "order_id" not in df and (one or df is orders):
            df["order_id"] = range(len(df))
            shop.notes.append("no order id: each row of the sales book is one order")
    shop.lines, shop.orders = _sales(lines, orders, shop.notes)


def apply_patch(manifest, path):
    """Lay a JSON manifest patch {"tables": {name: {"role"?, "cols": {column: field or null}}}} over a manifest.

    `path` may also be the patch itself (a manifest a run logged)."""
    if isinstance(path, dict):
        patch = path
    else:
        with open(path, encoding="utf-8") as f:
            patch = json.load(f)
    if patch:
        for name, spec in patch["tables"].items():
            t = manifest["tables"][name]
            t["cols"] = {c: v for c, v in {**t["cols"], **spec["cols"]}.items() if v}
            t["role"] = spec.get("role", t["role"])
            t["unknown"] = [c for c in t["unknown"] if c not in spec["cols"]]  # looked at, even if set to null
    return manifest


def build(tables, manifest, readme):
    shop = Shop(tables, manifest, readme)
    for n, spec in manifest["tables"].items():
        shop.by_role.setdefault(spec["role"], []).append(_canon(tables[n], spec))
    for role in ("lines", "orders"):
        if len(shop.by_role.get(role, [])) > 1:
            shop.notes.append(f"{len(shop.by_role[role])} tables look like {role}: using the biggest")
    steps = [setattr_sales, _toppings, _staff_names, _catalogue, _base_units, _customers, _note_debt, _side_tables, _ledgers]
    for step in steps:  # an odd shop must degrade, never crash the run: whatever was built stays usable
        try:
            step(shop)
        except Exception as e:  # noqa: BLE001
            shop.notes.append(f"{getattr(step, '__name__', 'sales')} failed: {e!r}")
    return shop


def open_shop(shop_dir, patch=None, tables=None):
    """Load a shop and build its views from the guessed manifest.

    patch: path of a JSON manifest patch {"tables": {name: {"role"?, "cols": {column: field or null}}}} laid over
    the guess. Development and training-data tooling only; inference never passes one.
    """
    tables, readme = tables or load_shop(shop_dir), read_readme(shop_dir)
    manifest = guess_manifest(tables, readme)
    if patch and os.path.exists(patch):
        apply_patch(manifest, patch)
    return build(tables, manifest, readme)
