"""Hand-written question templates. Training-data tooling only: nothing here runs at inference.

Given a train question, propose candidate programs over `m` (src.metrics.Metrics) and `P` (= m.P).
training_code/bank.py keeps a candidate only when its result equals the reference answer, so a loose
template costs nothing but time; a wrong one is simply never kept.
"""
import re

from src.parse import parse_number
from src.shop import key

PAYS = [("cash", r"tien mat|\btm\b"), ("transfer", r"chuyen khoan|\bck\b"), ("ewallet", r"vi dien tu"),
        ("cod", r"\bcod\b"), ("debt", r"ghi no")]  # fmt: skip
CHANS = [("zalo", r"zalo"), ("store", r"tai quay|tai tiem|tai cua hang|tai cho"), ("phone", r"dien thoai")]
MONTH = re.compile(r"P\.month\((\d+), (\d+)\)")
DAY = re.compile(r'P\.day\("([\d-]+)"\)')


def find_periods(f):
    """Period expressions named in a folded question, in order of appearance."""
    out = []

    def add(x):
        if x not in out:
            out.append(x)

    def year(y):
        return int(y) + 2000 if len(y) == 2 else int(y)

    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", f):
        add(f'P.day("{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}")')
    if len(out) == 2:  # "từ 14/12/2025 đến hết 20/12/2025"
        a, b = (DAY.fullmatch(x)[1] for x in out)
        out.insert(0, f'P.between("{a}", "{b}")')
    for m in re.finditer(r"\b(?:thang|th|t)\.? ?(\d{1,2}) ?(?:/|-| nam ) ?(\d{4}|\d{2})\b", f):
        add(f"P.month({year(m[2])}, {int(m[1])})")
    for m in re.finditer(r"(?<![\d/.-])(\d{1,2})[/-](\d{4})\b", f):
        add(f"P.month({m[2]}, {int(m[1])})")
    for m in re.finditer(r"\bquy (\d)(?: ?/ ?| nam | )(\d{4})\b", f):
        add(f"P.quarter({m[2]}, {m[1]})")
    for m in re.finditer(r"\btet (?:am lich )?(?:nam )?(\d{4})\b", f):
        add(f"P.tet({m[1]})")
    for m in re.finditer(r"\b(\d+) ngay (?:gan nhat|gan day|vua qua|qua)\b", f):
        add(f"P.last_days({m[1]})")
    for rx, expr in [
        (r"tu dau nam|tu (?:ngay )?1/1\b", "P.ytd()"),
        (r"hom qua|hqua|bua qua", "P.yesterday()"),
        (r"hom nay|hnay|bua nay|hom ni|bua ni", "P.today()"),
        (r"tuan (?:roi|truoc|vua roi|vua qua)", "P.last_week()"),
        (r"tuan nay", "P.this_week()"),
        (r"thang nay|thang ni", "P.this_month()"),
    ]:
        if re.search(rx, f):
            add(expr)
    return out


def month_run(f, periods):
    """Consecutive months a question lists or spans ("từ tháng 8/2025 tới tháng 10/2025", "3 tháng ... kết thúc ở")."""
    ym = [tuple(map(int, MONTH.fullmatch(p).groups())) for p in periods if MONTH.fullmatch(p)]
    if len(ym) == 2:  # the two ends of a span
        (y, mo), end, ym = ym[0], ym[1], []
        while (y, mo) <= end and len(ym) < 12:
            ym.append((y, mo))
            y, mo = (y, mo + 1) if mo < 12 else (y + 1, 1)
    elif len(ym) == 1 and (n := re.search(r"\b(\d) thang\b", f)):
        (y, mo), ym = ym[0], []
        for _ in range(int(n[1])):
            ym.insert(0, (y, mo))
            y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
    return "[" + ", ".join(f"P.month({y}, {mo})" for y, mo in ym) + "]" if len(ym) >= 2 else None


def named(f, rules):
    """Rule names whose pattern occurs in f, in order of appearance."""
    hits = [(m.start(), name) for name, rx in rules if (m := re.search(rx, f))]
    return [name for _, name in sorted(hits)]


def find_entities(shop, q):
    """Catalogue values named in the question (spelling-insensitive, longest match per kind)."""
    kq, lines, out = f" {key(q)} ", shop.lines, {}

    def longest(values):
        hits = [v for v in values if isinstance(v, str) and f" {key(v)} " in kq]
        return max(hits, key=len) if hits else None

    if shop.products is not None and "prod_name" in shop.products:
        out["prod"] = longest(shop.products["prod_name"])
    if lines is not None:
        cols = (("group", "group"), ("cgroup", "cust_group"), ("unit", "base_unit"), ("ingredient", "ingredient"))
        for name, col in cols:
            if col in lines:
                out[name] = longest(lines[col].dropna().unique())
    if out.get("prod") and out.get("group") and key(out["group"]) in key(out["prod"]):
        out["group"] = None  # "hải sản" inside "Gỏi khoai môn hải sản" is part of the product name
    people = lines if lines is not None else shop.orders
    if people is not None and "cust_name" in people:
        out["cust"] = longest(people["cust_name"].dropna().unique())
    if out.get("prod"):  # a big unit of that product named in the question: "mấy hộp chẵn"
        pid = shop.find_prod(out["prod"])
        bigs = [u for p, u in shop.factor if p == pid and f" {u} " in kq]
        out["big"] = max(bigs, key=len) if bigs else None
    return out


def candidates(f, typ, periods, e):
    """Programs worth trying. Order matters: the first one that reproduces the answer is kept.

    f: folded question; typ: reference answer type; e: find_entities() result.
    """
    out = []
    k = next((int(x) for x in re.findall(r"\b(?:top )?(\d{1,2}) (?:mat hang|san pham|sp|mon|hang|khach|kh)\b|\btop (\d{1,2})\b", f) for x in x if x), None)
    n_min = re.search(r"(?:tu|it nhat|toi thieu|≥|>=|\bo) ?(\d+) (?:don|bill|hoa don)", f)
    money = [int(v) for v in (parse_number(x) for x in re.findall(r"\d[\d.,]*\d ?(?:dong|d|k|tr)\b|\d+(?:tr|k)\d*", f)) if v and v >= 1000]
    pays, chans = named(f, PAYS), named(f, CHANS)
    size = re.search(r"\bsize (m|l)\b", f)
    staff = re.search(r"\bnv ?\d+\b", f)
    filt = "".join(f", {n}={e[n]!r}" for n in ("prod", "group", "cgroup") if e.get(n)).replace("cgroup=", "cust_group=")
    extra = [f", shift={s!r}" for s in (("sáng",) if "ca sang" in f else ()) + (("chiều",) if "ca chieu" in f else ())]
    extra += [f", pay={x!r}" for x in pays] + [f", chan={x!r}" for x in chans]
    extra += [f", size={size[1].upper()!r}"] if size else []
    extra += [f", staff={staff[0].replace(' ', '').upper()!r}"] if staff else []
    if re.search(r"ke don|don bac si", f):
        extra.append(f", rx={not re.search(r'(?:khong|ko|hok|hong|k) (?:ke|can) don|tru thuoc ke don', f)}")
    if e.get("ingredient"):
        extra.append(f", ingredient={e['ingredient']!r}")
    one = extra[0] if len(extra) == 1 else ""  # a single extra filter applies to every metric
    months = month_run(f, periods)
    days = [DAY.fullmatch(p)[1] for p in periods if DAY.fullmatch(p)]
    span = re.search(r"\b(\d+) ngay\b", f)

    day = repr(days[0]) if days else ""  # "đến hết ngày X"; empty = today
    also_day = f", {day}" if day else ""
    if re.search(r"\bno\b|con thieu|cong no|mua chiu", f):  # §4 debt at the end of a day
        if typ == "table":
            out.append(f"m.top_debtors({k or 3}{also_day})")
        if typ == "number":
            if re.search(r"(?:so|bao nhieu|bao nhiu|may|bn|bnhiu) khach", f):
                out.append(f"m.n_debtors({day})")
            if age := re.search(r"(?:qua|tren|hon) (\d+) ngay", f):
                out.append(f"m.debt_aging([{age[1]}]{also_day})[1]")
            out.append(f"m.debt_total({day})")
        if typ == "list" and (b := re.findall(r"(\d+) ?[–-] ?(\d+) ngay", f)):
            out.append(f"m.debt_aging({[int(hi) for _, hi in b]}{also_day})")
    if e.get("prod") and re.search(r"\bton\b|trong kho|\bcon\b", f):  # §5 stock at the end of today
        if typ == "number":
            out.append(f"m.stock({e['prod']!r})")
        if typ == "list" and e.get("big"):
            out.append(f"m.stock_split({e['prod']!r}, {e['big']!r})")
    if typ == "number" and re.search(r"gia von|gia tri", f) and re.search(r"kho|ton", f):
        out.append("m.stock_value()")
    if typ == "number" and "het hang" in f:
        out.append("len(m.out_of_stock())")
    if re.search(r"han dung|het han", f):  # §6 lots
        if typ == "date" and e.get("prod"):
            out.append(f"m.nearest_expiry({e['prod']!r})")
        if typ == "list" and periods and periods[0].startswith("P.between"):
            out.append(f"m.expiring({periods[0]})")
    if typ == "percent" and "tet" in f:
        for y in re.findall(r"P\.tet\((\d+)\)", " ".join(periods)):
            out.append(f"m.pct_change(m.revenue(P.tet({y})), m.revenue(P.before_tet({y})))")
            out.append(f"m.pct_change(m.revenue(P.tet({y})), m.revenue(P.tet({int(y) - 1})))")
    if typ == "number" and e.get("prod") and days and "gia" in f:
        out.append(f"m.price_on({e['prod']!r}, {days[0]!r})")
    if typ == "percent" and e.get("prod") and days and span and re.search(r"gia", f):  # §5 hiệu ứng tăng giá
        d, n, pr = days[0], span[1], e["prod"]
        out.append(f"m.pct_change(m.qty(P.days_from({d!r}, {n}), prod={pr!r}), m.qty(P.days_before({d!r}, {n}), prod={pr!r}))")
    if typ == "percent" and e.get("prod") and not days and re.search(r"gia", f):  # the raise day is not given
        n = span[1] if span else (re.search(r"\b(\d+) ngay", f) or [None, "7"])[1]
        for v in (parse_number(x) for x in re.findall(r"\d[\d.,]*\d ?(?:dong|d|k)\b|\d+k\b", f)):
            if v and v >= 100:
                d, pr = f"m.price_raise_day({e['prod']!r}, {v:g})", e["prod"]
                out.append(f"m.pct_change(m.qty(P.days_from({d}, {n}), prod={pr!r}), m.qty(P.days_before({d}, {n}), prod={pr!r}))")
    if typ == "percent" and len(periods) == 2 and re.search(r"so (?:voi|vs)|tang truong", f):  # two named periods
        out.append(f"m.pct_change(m.revenue({periods[0]}), m.revenue({periods[1]}))")

    for p in periods:
        if "nhap" in f:  # §5 purchases
            if typ == "number" and e.get("prod") and re.search(r"binh quan|trung binh", f):
                out.append(f"m.avg_purchase_price({p}, {e['prod']!r})")
            if typ == "number" and e.get("prod") and re.search(r"\btru\b", f):
                out.append(f"m.purchased_qty({p}, {e['prod']!r}) - m.qty({p}, prod={e['prod']!r})")
            if typ == "number":
                out.append(f"m.purchase_total({p})")
            if typ == "string":
                out.append(f"m.top_suppliers({p}, 1)[0]")
        if typ == "number" and re.search(r"\bdot\b", f):
            out.append(f"m.n_deliveries({p})")
        if typ == "string" and re.search(r"nhan vien|nguoi ban", f):
            out.append(f"m.top_staff({p}, 1)[0]")
        if typ == "number" and re.search(r"thu hoi|tra no|thu no|tra bot|tra duoc", f):
            out.append(f"m.repaid({p})")
        if typ == "number" and re.search(r"no them|chiu them", f):
            out.append(f"m.new_debt({p})")
        if typ == "list" and e.get("cust") and re.search(r"\bno\b|mua chiu", f):
            out.append(f"[m.new_debt({p}, cust={e['cust']!r}), m.repaid({p}, cust={e['cust']!r})]")
        if typ == "number":
            if re.search(r"lai rong|loi rong", f):
                out.append(f"m.net_profit({p})")
            if re.search(r"\blai\b|\bloi\b", f):
                out.append(f"m.gross_profit({p}{filt})")
            if re.search(r"trung binh|binh quan", f):
                out += [f"m.avg_order({p}{filt}{one})", f"m.avg_order({p})"]
            if re.search(r"\bhuy\b", f):
                out.append(f"m.n_cancelled({p}{one})")
            if n_min and re.search(r"khach", f):
                out.append(f"int((m.cust_orders({p}) >= {n_min[1]}).sum())")
            if n_min and re.search(r"mat hang|san pham|\bsp\b|\bmon\b", f):
                out.append(f"int((m.prod_orders({p}) >= {n_min[1]}).sum())")
            if money and re.search(r"\b(don|bill)\b", f):
                out.append(f"int((m.order_values({p}) >= {money[0]}).sum())")
            if money and "ngay" in f:
                out.append(f"int((m.daily({p}) >= {money[0]}).sum())")
            if re.search(r"khong ban|ko ban|k ban|\be\b", f):
                out.append(f"len(m.unsold({p}))")
            if re.search(r"\b(don|bill|order)\b", f):
                out += [f"m.n_orders({p}{one})", f"m.n_orders({p})"]
            if e.get("unit") or size:
                unit = f", unit={e['unit']!r}" if e.get("unit") else ""
                out += [f"m.qty({p}{filt}{one})", f"m.qty({p}{unit}{filt}{one})"]
            out += [f"m.revenue({p}{filt}{x})" for x in extra]
            if filt:
                out.append(f"m.revenue({p}{filt})")
            out.append(f"m.revenue({p})")
        if typ == "percent":
            if re.search(r"\bhuy\b", f):
                out.append(f"m.cancel_rate({p})")
            if n_min and "khach" in f:
                out.append(f"m.share(int((m.cust_orders({p}) >= {n_min[1]}).sum()), len(m.cust_orders({p})))")
            if "ca sang" in f and "ca chieu" in f:
                what = "n_orders" if re.search(r"\b(don|bill)\b", f) else "revenue"
                out.append(f"m.pct_change(m.{what}({p}, shift='chiều'), m.{what}({p}, shift='sáng'))")
            if filt or one:
                out.append(f"m.share(m.revenue({p}{filt}{one}), m.revenue({p}))")
            if "cung ky" in f:
                out.append("m.pct_change(m.revenue(P.this_month()), m.revenue(P.same_days_last_month()))")
            if re.search(r"nam truoc|nam ngoai", f) and (ym := MONTH.fullmatch(p)):
                out.append(f"m.pct_change(m.revenue({p}), m.revenue(P.month({int(ym[1]) - 1}, {ym[2]})))")
            out.append(f"m.pct_change(m.revenue({p}), m.revenue(P.prev({p})))")
        if typ == "date":
            out.append(f"m.best_day({p})")
        if typ in ("string", "list"):
            pick = "[0]" if typ == "string" else ""
            n = 1 if typ == "string" else k or 3
            who = [f"m.top_customers({p}, {n})", f"m.top_customers({p}, {n}, by='orders')"]
            what = [f"m.top_products({p}, {n}{filt})", f"m.top_products({p}, {n}, by='amount'{filt})"]
            if re.search(r"\blai\b|\bloi\b", f):
                what.insert(0, f"m.top_products({p}, {n}, by='profit'{filt})")
            if "nhan vien" in f:
                what.insert(0, f"m.top_staff({p}, {n})")
            out += [x + pick for x in (who + what if re.search(r"khach|\bai\b", f) else what + who)]
        if typ == "list" and len(extra) == 2:  # "doanh thu các đơn chuyển khoản và các đơn tiền mặt"
            out.insert(0, f"[m.revenue({p}{extra[0]}), m.revenue({p}{extra[1]})]")
    if months:
        if typ == "date":
            out.insert(0, f"m.best_month({months})")
        if typ == "boolean":
            out.append(f"m.rising({months})")
        if typ == "list":
            out.insert(0, f"[m.revenue(p) for p in {months}]")
    return list(dict.fromkeys(out))
