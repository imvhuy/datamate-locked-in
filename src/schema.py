"""Schema layer: decide what each loaded table and column means (the manifest), then build canonical views.

A manifest is plain JSON so an LLM can write or patch it later:
    {"tables": {"SoBanHang": {"role": "lines", "cols": {"Mã hóa đơn": "order_id", ...}}, ...}}
`guess_manifest` is the deterministic first pass: README column descriptions, then a column-name lexicon.
"""
import datetime as dt
import json
import os
import re

from src.parse import fold

# (pattern matched at the START of the folded README description, canonical field). First match wins.
DESC_RULES = [
    (r"cot trong, khong co du lieu", "empty"),
    (r"so thu tu dong trong phieu nhap", "po_line_no"),
    (r"so thu tu dong trong don", "line_no"),
    (r"ma don|so hoa don|so thu tu don", "order_id"),
    (r"ma phieu nhap", "po_id"),
    (r"ma phieu thu", "pay_id"),
    (r"ma nha cung cap", "supplier_id"),
    (r"ma nhan vien ban", "staff"),
    (r"ma khach", "cust_id"),
    (r"ma hang", "prod_id"),
    (r"ten hang|hang ban", "prod_name"),
    (r"ten khach", "cust_name"),
    (r"ten$", "name"),  # product or customer list: settled by the table's role
    (r"gia nhap mot don vi", "po_price"),
    (r"so luong da doi ra don vi co ban", "base_qty"),
    (r"so luong nhap \(don vi co ban\)", "lot_in_qty"),
    (r"so luong nhap", "po_qty"),
    (r"so luong ban lay tu lo", "lot_qty"),
    (r"so luong khach tra lai", "lot_ret_qty"),
    (r"so luong", "qty"),
    (r"don vi co ban", "base_unit"),
    (r"don vi lon", "big_unit"),
    (r"don vi nhap", "po_unit"),
    (r"don vi tinh", "unit"),
    (r"he so", "factor"),
    (r"ton kho", "stock_qty"),
    (r"thoi diem dat don|gio dat don", "ts"),
    (r"thoi diem thu", "pay_ts"),
    (r"ngay bat dau ap dung gia", "price_date"),
    (r"ngay ban", "date"),
    (r"ngay nhap", "po_date"),
    (r"ngay giao", "deliv_date"),
    (r"han su dung", "expiry"),
    (r"han thanh toan", "due_date"),
    (r"so ngay duoc no", "credit_days"),
    (r"gia truoc lan doi", "old_price"),
    (r"gia ban ap dung tu ngay", "new_price"),
    (r"gia ban hien tai", "list_price"),
    (r"gia von", "cost"),
    (r"don gia", "price"),
    (r"thanh tien sau giam|thanh tien cua dong", "amount"),
    (r"tien nhap cua ca dong", "po_amount"),
    (r"tong tien phieu nhap", "po_total"),
    (r"tong tien cua ca phieu", "slip_total"),  # a sales slip or a purchase slip: settled by the table's role
    (r"tong tien hang|tien hang cua don", "order_goods"),
    (r"tong tien don|khach can tra|so khach phai tra", "order_total"),
    (r"giam gia cua ca don", "order_disc"),
    (r"tien giam cua ca dong", "line_disc"),
    (r"giam gia", "disc"),  # line or order level: settled by the table's role
    (r"phi giao hang", "ship"),
    (r"da thanh toan|khach da tra cho ca don", "paid"),
    (r"con no|so khach con no|khach con no", "debt"),
    (r"so tien$", "pay_amount"),
    (r"trang thai phieu nhap", "po_status"),
    (r"trang thai don", "status"),
    (r"hinh thuc thanh toan", "payment"),
    (r"kenh ban", "channel"),
    (r"nhom khach", "cust_group"),
    (r"nhom hang", "group"),
    (r"nha cung cap", "supplier"),
    (r"so lo", "lot"),
    (r"hoat chat", "ingredient"),
    (r"thuoc ke don", "rx"),
    (r"dot giao", "deliv_no"),
    (r"so dien thoai", "phone"),
    (r"dia chi", "address"),
    (r"ghi chu", "note"),
]

# Which fields make a table play which role. Checked in order.
ROLE_RULES = [
    ("payments", {"pay_amount", "pay_ts", "pay_id"}),
    ("lot_sales", {"lot_qty", "lot_ret_qty"}),
    ("deliveries", {"deliv_date", "deliv_no"}),
    ("terms", {"due_date", "credit_days"}),
    ("prices", {"new_price", "price_date"}),
    ("returns", {"refund", "ret_ts"}),
    ("ledger", {"stock_after", "led_ts", "led_type"}),
    ("expenses", {"exp_amount"}),
    ("lots", {"lot_in_qty"}),
    ("purchases", {"po_id", "po_qty", "po_price", "po_amount", "po_date", "po_total"}),
    ("units", {"factor", "big_unit"}),
    ("stock", {"stock_qty"}),
    ("drugs", {"ingredient", "rx"}),
]


def read_readme(shop_dir):
    """-> {"text", "sections": [(file_pattern, sheet or None, {col: desc})], "data_end": date or None}."""
    path = os.path.join(shop_dir, "README.md")
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    sections, cur = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = None
            if m := re.match(r"## Cột trong `(.+?)`(?:, sheet `(.+?)`)?", line):
                cur = (m[1], m[2], {})
                sections.append(cur)
        elif cur and (m := re.match(r"- \*\*(.+?)\*\*: (.+)", line)):
            cur[2][" ".join(m[1].split())] = m[2].strip()
    end = re.search(r"có đến hết (\d{1,2})/(\d{1,2})/(\d{4})", text)
    return {
        "text": text,
        "sections": sections,
        "data_end": dt.date(int(end[3]), int(end[2]), int(end[1])) if end else None,
    }


def _docs_for(table, readme):
    """README column descriptions that apply to this table: {col: desc}."""
    out = {}
    for pattern, sheet, cols in readme["sections"]:
        rx = re.compile(re.sub(r"<.+?>", ".+", re.escape(pattern).replace(r"\<", "<").replace(r"\>", ">")))
        if any(rx.fullmatch(f) and (sheet is None or sheet == s) for f, s, _ in table.parts):
            out.update(cols)
    return out


def field_of(desc):
    d = fold(desc)
    return next((f for rx, f in DESC_RULES if re.match(rx, d)), None)


def role_of(fields):
    f = set(fields)
    for role, keys in ROLE_RULES:
        if f & keys and not (role == "purchases" and f & {"order_id", "cust_name", "cust_id"}):  # that is a sale
            return role
    has_prod = f & {"prod_name", "prod_id"}
    if has_prod and f & {"qty", "amount", "base_qty"}:
        return "lines"
    if "order_id" in f:
        return "orders"
    if has_prod or f & {"group", "base_unit", "list_price"}:
        return "products"
    if f & {"cust_id", "cust_group", "phone"}:
        return "customers"
    return "other"


LEXICON_PATH = os.path.join(os.path.dirname(__file__), "lexicon.json")
_lexicon = None


def compact(name):
    """'Mã hóa đơn', 'MaHoaDon', 'ma_hoa_don' -> 'mahoadon'."""
    return re.sub(r"[^a-z0-9]", "", fold(name))


def load_lexicon():
    """Column-name lexicon learned from the train shops (training_code/lexicon.py): {name: {"role|field": n}}."""
    global _lexicon
    if _lexicon is None:
        _lexicon = {}
        if os.path.exists(LEXICON_PATH):
            with open(LEXICON_PATH, encoding="utf-8") as f:
                _lexicon = json.load(f)
    return _lexicon


def _pick(entry, role=None):
    """Most frequent field of a lexicon entry, among the given role's uses when there are any."""
    pool = {k: n for k, n in entry.items() if k.startswith(f"{role}|")} or entry
    return max(pool, key=pool.get).split("|")[1]


ORDER_KEY = re.compile(r"các dòng cùng \*\*(.+?)\*\* là một (?:đơn|phiếu|hóa đơn|hoá đơn)", re.IGNORECASE)


def guess_table(table, readme, lexicon=None):
    """-> {"role", "cols": {column: field}, "unknown": [columns with no field], "documented": README describes it}.

    A column takes its field from the README description when there is one, else from the name lexicon.
    """
    lexicon = load_lexicon() if lexicon is None else lexicon
    docs = _docs_for(table, readme)
    cols, by_name = {}, {}
    for c in table.kinds:
        if c in docs:
            f = field_of(docs[c])
        elif entry := lexicon.get(compact(c)):
            f = by_name[c] = _pick(entry)
        else:
            f = None
        if f and f != "empty":
            cols[c] = f
    role = role_of(cols.values())
    for c in by_name:  # same name, other meaning in another kind of table ("Thành tiền", "Số lượng", "Trạng thái")
        cols[c] = _pick(lexicon[compact(c)], role)
    role = role_of(cols.values())
    if role in ("lines", "orders"):  # README: "các dòng cùng **STT** là một đơn" names the order key
        for c in set(ORDER_KEY.findall(readme.get("text", ""))) & set(table.kinds):
            cols = {k: f for k, f in cols.items() if f != "order_id"} | {c: "order_id"}
        role = role_of(cols.values())
    if role == "customers" and "supplier_id" in cols.values() and "cust_id" not in cols.values():
        role = "suppliers"  # a contact list keyed by supplier code
    # Fields whose meaning depends on the table they sit in.
    fix = {
        "name": "cust_name" if role == "customers" else "prod_name",
        "disc": "line_disc" if role == "lines" and {"prod_name", "prod_id"} & set(cols.values()) else "order_disc",
        "slip_total": "po_total" if role == "purchases" else "order_total",
    }
    if role == "products":
        fix["cost"] = "prod_cost"
    # The same word means another field in another kind of table; None drops a mapping that does not belong.
    fix.update(
        {
            "products": {"ts": None, "status": None},
            "lines": {"prod_cost": "cost"},
            "customers": {"status": None},
            "purchases": {"qty": "po_qty", "unit": "po_unit", "price": "po_price", "date": "po_date",
                          "status": "po_status", "amount": "po_amount", "base_unit": "po_unit"},
            "payments": {"date": "pay_ts", "ts": "pay_ts"},
            "ledger": {"stock_qty": "stock_after", "base_qty": "led_qty", "order_id": "doc_id", "date": "led_ts", "ts": "led_ts"},
            "lot_sales": {"base_qty": "lot_qty", "po_line_no": "line_no"},
            "suppliers": {"cust_name": "supplier", "name": "supplier"},
        }.get(role, {})
    )  # fmt: skip
    cols = {c: fix.get(f, f) for c, f in cols.items() if fix.get(f, f)}
    unknown = [c for c in table.kinds if c not in cols and field_of(docs.get(c, "")) != "empty"]
    role = role if role == "suppliers" else role_of(cols.values())
    return {"role": role, "cols": cols, "unknown": unknown, "documented": bool(docs)}


def guess_manifest(tables, readme, lexicon=None):
    return {"tables": {n: guess_table(t, readme, lexicon) for n, t in tables.items()}}


if __name__ == "__main__":
    import sys

    from src.load import load_shop

    for shop in sys.argv[1:]:
        m = guess_manifest(load_shop(shop), read_readme(shop))
        print("=====", os.path.basename(shop))
        for n, t in m["tables"].items():
            print(f"  [{n}] {t['role']}: {json.dumps(t['cols'], ensure_ascii=False)}")
            if t["unknown"]:
                print(f"      unknown: {t['unknown']}")
