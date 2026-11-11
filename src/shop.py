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
    for name, col in (("cust", "cust_id"), ("prod", "prod_id")):  # identity: the code when there is one, else the name
        alt = col.replace("_id", "_name")
        if col in src:
            src[name] = src[col]
        elif alt in src:  # no such column at all: leave it out, so asking about it is "unanswerable"
            src[name] = src[alt].map(key, na_action="ignore")
    if "cust_name" in src:
        src.loc[src["cust_name"].map(lambda v: isinstance(v, str) and key(v) in WALKIN), "cust"] = None

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
        out["order_goods"] = sold.groupby("order_id")["amount"].sum().reindex(out.index).fillna(0)
    return src, out.reset_index()


def _catalogue(shop):
    """Product list keyed the way sales lines identify products, and its attributes copied onto the lines."""
    lines, cat = shop.lines, _biggest(shop.by_role.get("products"))
    if lines is None or "prod" not in lines or lines["prod"].isna().all():
        return
    idcol = "prod_id" if "prod_id" in lines else "prod_name"
    if cat is not None and idcol in cat:
        cat = cat.assign(prod=cat[idcol] if idcol == "prod_id" else cat[idcol].map(key, na_action="ignore"))
    else:  # no product list: the products are whatever was sold
        cols = [c for c in ("prod_name", "group", "unit") if c in lines]
        cat = lines.groupby("prod", sort=False)[cols].first().reset_index()
    cat = cat.dropna(subset=["prod"]).drop_duplicates("prod").reset_index(drop=True)
    cat["pkey"] = cat["prod_name"].map(key, na_action="ignore") if "prod_name" in cat else None
    add = [c for c in ("prod_name", "group", "base_unit") if c in cat and c not in lines]
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
        for col, raw, rules in (("pay", "payment", PAY), ("chan", "channel", CHANNEL)):
            if raw in df:  # one vocabulary whatever the shop writes: "Tiền mặt" / "cash" -> cash
                df[col] = df[raw].map({v: label(v, rules) for v in df[raw].dropna().unique()})
        if attr == "lines" and {"cost", "qty", "amount"} <= set(df.columns):
            df["profit"] = df["amount"] - df["qty"] * df["cost"]  # §5: cost is per selling unit of the line
        setattr(shop, attr, df)


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
    """Lay a JSON manifest patch {"tables": {name: {"role"?, "cols": {column: field or null}}}} over a manifest."""
    with open(path, encoding="utf-8") as f:
        for name, spec in json.load(f)["tables"].items():
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
    steps = [setattr_sales, _catalogue, _base_units, _customers, _side_tables]
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
