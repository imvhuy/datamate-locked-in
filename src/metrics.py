"""Rulebook metrics as code. One Metrics object per question:

    m = Metrics(shop, as_of, lunar)      # m.P builds periods
    m.revenue(m.P.month(2025, 7))
    m.revenue(m.P.last_week(), prod="Sting dâu 330ml")
    m.cancel_rate(m.P.this_month())

Every method takes a period (start, end). Records dated after as_of have not happened yet and are ignored.

Filters (`**where`) accepted wherever a method says so:
    prod="name or code"   cust="name or code"   group="nhóm hàng"   cust_group="nhóm khách"   staff="NV001"
    shift="sáng" | "chiều"   size="M"   pay="cash|transfer|ewallet|cod|debt"   chan="store|phone|zalo|ecom"
    size="XL" / so=2 (the catalogue's Số / Size column when it has one)   brand="Huggies" (catalogue Hãng, else first word)
    line="iPhone 13" (dòng máy)   phone=True (phones only, no accessories)
"""
import collections
import datetime as dt
import itertools
import math
import re

import pandas as pd

from src.ledger import norm_id
from src.mapper import FIELDS
from src.parse import fold
from src.period import Periods
from src.shop import PLATE_NOTE, VALID, cust_key, key


class NoData(Exception):
    """The released data cannot answer this (rulebook §7): the answer is `unanswerable`, never 0."""


# Columns a shop view may lack because the shop never recorded them; a KeyError on anything else is a bug.
CANON = set(FIELDS) | {"cust", "prod", "st", "o_status", "o_date", "o_ts", "pay", "chan", "is_item", "profit", "pkey"}


# The rule (row name in the training rulebook) each method was written for. The prompt shows it next to the method,
# so the model uses a method only for that rule and writes its own code for any rule no method implements.
IMPLEMENTS = {
    "sold": "Đơn tính vào doanh thu", "orders": "Số đơn", "revenue": "Doanh thu một đơn", "n_orders": "Số đơn",
    "n_cancelled": "Đơn bị huỷ", "cancel_rate": "Tỉ lệ đơn huỷ trong kỳ", "avg_order": "Giá trị trung bình một đơn",
    "pct_change": "Phần trăm thay đổi, tăng trưởng", "qty": "Bán được bao nhiêu <đơn vị>",
    "top_products": "Bán chạy (by='base_qty'), Hàng lời nhất (by='profit')", "unsold": "Mặt hàng không bán được trong kỳ (ế)",
    "top_customers": "Xác định khách; khách mua nhiều tiền nhất", "top_staff": "Nhân viên bán giỏi nhất; Thợ làm nhiều nhất (by='tickets')",
    "gross_profit": "Lãi gộp", "net_profit": "Lãi ròng một tháng", "purchase_total": "Tiền nhập hàng trong kỳ",
    "top_suppliers": "Nhà cung cấp nhập nhiều nhất (theo tiền nhập)",
    "avg_purchase_price": "Giá nhập bình quân một mặt hàng trong tháng", "stock_split": "Tồn theo đơn vị lớn",
    "stock_value": "Giá trị hàng tồn kho", "out_of_stock": "Mặt hàng hết hàng cuối ngày", "price_on": "Giá bán X ngày D",
    "price_raise_day": "Hiệu ứng tăng giá", "debts": "Nợ một đơn tại ngày X", "debt_total": "Nợ một đơn tại ngày X",
    "debt_by_customer": "Nợ theo khách", "top_debtors": "Nợ theo khách", "n_debtors": "Nợ theo khách",
    "debt_aging": "Tuổi nợ", "new_debt": "Nợ thêm, trả nợ, thu nợ trong kỳ", "repaid": "Nợ thêm, trả nợ, thu nợ trong kỳ",
    "expiring_lots": "Hàng hết hạn trong N ngày tới", "nearest_expiry": "Lô gần hết hạn nhất của một thuốc",
    "n_deliveries": "Giao nhiều đợt",
    "loyal_customers": "Khách trung thành nhất", "big_spenders": "Khách sộp nhất", "fickle_customers": "Khách hay đổi ý nhất",
    "popular_products": "Mặt hàng được ưa chuộng nhất", "hardest_workers": "Nhân viên, thợ chăm chỉ nhất",
    "busiest_day": "Ngày đắt khách nhất", "peak_hour": "Giờ cao điểm", "weekday_avg": "Trung bình mỗi ngày thứ X của kỳ",
    "main_supplier": "Nhà cung cấp chính", "charges": "Tiền giảm voucher, tiền giảm nhờ dùng điểm, phí ship",
    "trade_ins": "Thu máy cũ", "customer_pays": "Thu máy cũ", "installments": "Trả góp", "phones": "Dòng máy, hãng",
    "top_lines": "Dòng máy, hãng", "warranty_rate": "Bảo hành", "warranty_visits": "Bảo hành",
    "new_members": "Khách thành viên mới trong tháng", "tickets": "Lượt xe, phiếu", "avg_ticket": "Lượt xe, phiếu",
    "jobs": "Việc ghi trong ô Làm gì", "vehicles": "Dòng xe, biển số", "top_vehicle_lines": "Dòng xe, biển số",
    "regulars": "Khách quen, xe tới nhiều lần", "delivered": "Giao nhiều đợt",
}  # fmt: skip
# Methods for rules that only some rulebooks have: the prompt shows them only when the split's rulebook has the rule.
OPTIONAL = {"loyal_customers", "big_spenders", "fickle_customers", "popular_products", "hardest_workers", "busiest_day",
            "peak_hour", "weekday_avg", "main_supplier", "charges", "trade_ins", "customer_pays", "installments", "phones",
            "top_lines", "warranty_rate", "warranty_visits", "new_members", "tickets", "avg_ticket", "jobs", "vehicles",
            "top_vehicle_lines", "regulars"}  # fmt: skip


def is_no_data(e):
    """True when an error means "unanswerable": NoData, or a canonical column the shop does not have."""
    return isinstance(e, NoData) or (isinstance(e, KeyError) and bool(e.args) and e.args[0] in CANON)


def half_up(x, digits=0):
    """Rulebook rounding: money to the đồng with halves up, percents and averages to 2 decimals."""
    q = 10**digits
    v = math.floor(x * q + 0.5 + 1e-9) / q
    return int(v) if digits == 0 else v


def as_ts(d):
    """A day given as a date, text or a P period (then its last day) -> Timestamp."""
    return pd.Timestamp(d[1] if isinstance(d, tuple) and len(d) == 2 else d)


def tidy(x):
    """A count stays an int, anything else keeps 2 decimals."""
    r = round(float(x))
    return r if abs(x - r) < 1e-6 else half_up(x, 2)


class Metrics:
    def __init__(self, shop, as_of, lunar=None):
        self.shop = shop
        self.P = Periods(as_of, lunar)
        self.as_of = pd.Timestamp(self.P.as_of)
        sales = shop.lines if shop.lines is not None else shop.orders
        end = shop.readme.get("data_end")
        self.first = sales["o_date"].min() if sales is not None else None  # the data covers first..last
        self.last = pd.Timestamp(end) if end else (sales["o_date"].max() if sales is not None else None)

    def _have(self, attr):
        """A shop table, or NoData when the shop does not keep that kind of record."""
        df = getattr(self.shop, attr)
        if df is None:
            raise NoData(f"the shop keeps no {attr}")
        return df

    def _role(self, name):
        frames = self.shop.by_role.get(name)
        if not frames:
            raise NoData(f"the shop keeps no {name}")
        return frames[0]

    # --- row selection -------------------------------------------------------------------------------
    def _within(self, df, p, col="o_date"):
        a, b = pd.Timestamp(p[0]), min(pd.Timestamp(p[1]), self.as_of)
        if a > self.as_of:  # nobody asks about the future: the period is a misreading, not missing data
            raise ValueError(f"{p[0]}..{p[1]} starts after today ({self.as_of.date()})")
        if self.first is not None and (pd.Timestamp(p[1]) < self.first or a > self.last):
            raise NoData(f"{p[0]}..{p[1]} lies wholly outside the data ({self.first.date()}..{self.last.date()})")
        return df[(df[col] >= a) & (df[col] < b + pd.Timedelta(days=1))]

    def rows(self, df, col, p):
        """Rows of any table (e.g. T["SoSuaXe"]) whose date column `col` lies in the period, never after today."""
        if col not in df.columns:  # a canonical name ("date") or another spelling of the table's own date column
            dates = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
            same = [c for c in dates if fold(str(c)) == fold(str(col))]
            if same or len(dates) == 1:
                col = (same or dates)[0]
            else:
                raise KeyError(f"no column {col!r}; the date columns are {dates}")
        a, b = pd.Timestamp(p[0]), min(pd.Timestamp(p[1]), self.as_of)
        return df[(df[col] >= a) & (df[col] < b + pd.Timedelta(days=1))]

    def with_order(self, df, col):
        """Rows of any table whose column `col` names a sales order (26, 26.0, "STT 26"), with that order's order_id,
        o_date, o_status and `counted` (True: the order is not cancelled and not returned, §1)."""
        o = self._have("orders")
        ids = o.assign(_k=o["order_id"].map(norm_id))[["_k", "order_id", "o_date", "o_status"]].drop_duplicates("_k")
        out = df.drop(columns=[c for c in ("order_id", "o_date", "o_status") if c in df]).assign(_k=df[col].map(norm_id))
        out = out.merge(ids, on="_k", how="left").drop(columns="_k")
        out["counted"] = out["o_status"].isin(VALID)
        return out

    def _filter(self, df, where):
        for col, want in where.items():
            if col == "prod":
                df = df[df["prod"] == self.shop.find_prod(want)]
            elif col == "cust":  # a code matches as is, a name through its spelling-insensitive key
                df = df[(df[col] == want) | (df[col] == cust_key(want))]
            elif col == "staff":  # a code, or a name looked up in the staff list
                names = next(iter(self.shop.by_role.get("staff", [])), None)
                if names is not None and {"staff", "staff_name"} <= set(names.columns) and want not in set(df["staff"]):
                    want = dict(zip(names["staff_name"].map(key), names["staff"])).get(key(want), want)
                df = df[df["staff"] == want]
            elif col == "shift":  # §2: ca sáng 06:00-13:59, ca chiều 14:00-21:59, by order creation time
                lo, hi = (6, 14) if key(want) == "sang" else (14, 22)
                df = df[df["o_ts"].dt.hour.between(lo, hi - 1)]
            elif col in ("size", "so") and (attr := find_col(df, part=("size", "socolon", "sosize"))):
                df = df[df[attr].map(lambda v, w=want: attr_is(v, w))]  # §6 mẹ và bé: the catalogue's Số / Size column
            elif col == "size":  # §6 cà phê: the name ends with "(M)" / "(L)", any letter case
                df = df[df["prod_name"].str.lower().str.endswith(f"({want.lower()})", na=False)]
            elif col == "brand":  # a catalogue brand column (Hãng), else the first word of the name (§6 điện thoại)
                b = find_col(df, exact=("hang", "thuonghieu", "hangsx", "nhasanxuat"))
                b = b if b and df[b].nunique() <= 60 else None
                df = df[(df[b] if b else df["prod_name"].map(brand_of)).map(lambda v, w=want: key(str(v)) == key(str(w)))]
            elif col == "line":  # §6 điện thoại dòng máy
                df = df[df["prod_name"].map(lambda v, w=want: key(str(model_line(v))) == key(str(w)))]
            elif col == "phone":  # phones only (True) or accessories only (False)
                df = df[is_phone(df) == bool(want)]
            elif col in ("pay", "chan") or isinstance(want, bool):  # rx=True / rx=False
                df = df[df[col] == want]
            else:
                df = df[df[col].map(key, na_action="ignore") == key(want)]
        return df

    def sold(self, p, **where):
        """Sales lines of orders that count as revenue (§1: not cancelled, no return) placed in the period."""
        if self.shop.lines is None and self.shop.orders is not None and "order_goods" in self.shop.orders:
            df = self.shop.orders.assign(amount=self.shop.orders["order_goods"])  # order-level book: one line per order
        else:
            df = self._have("lines")
            if "charge" in df:  # voucher, points, shipping rows: money of the order, not goods (§1)
                df = df[~df["charge"]]
        return self._filter(self._within(df[df["o_status"].isin(VALID) & (df["st"] != "return")], p), where)

    def items(self, p, **where):
        """sold() without rows that are not goods (§3: the 'Phí phục vụ' row)."""
        df = self.sold(p, **where)
        return df[df["is_item"]] if "is_item" in df else df

    def orders(self, p, status=VALID, **where):
        """Orders placed in the period with the given statuses (status=None: every status)."""
        df, lines = self._within(self._have("orders"), p), self.shop.lines
        # "đơn có mua hàng nhóm X": a filter on what an order holds keeps the orders with at least one such line
        held = {c: w for c, w in where.items() if c not in df and lines is not None and c in lines} if "order_id" in df and lines is not None and "order_id" in lines else {}
        if held:
            df = df[df["order_id"].isin(self._filter(lines, held)["order_id"])]
        df = self._filter(df, {c: w for c, w in where.items() if c not in held})
        return df if status is None else df[df["o_status"].isin(status)]

    # --- §1 revenue and orders ------------------------------------------------------------------------
    def revenue(self, p, **where):
        if self.shop.lines is None:  # order-level export only
            return half_up(self.orders(p, **where)["order_goods"].sum())
        return half_up(self.sold(p, **where)["amount"].sum())

    def n_orders(self, p, **where):
        return len(self.orders(p, **where))

    def n_cancelled(self, p, **where):
        return len(self.orders(p, status=("cancel",), **where))

    def cancel_rate(self, p):
        """% of cancelled orders among every order placed in the period, whatever its status."""
        return half_up(100 * self.n_cancelled(p) / len(self.orders(p, status=None)), 2)

    def avg_order(self, p, **where):
        return half_up(self.revenue(p, **where) / self.n_orders(p, **where))

    def order_values(self, p, **where):
        """Revenue of each valid order (Series indexed by order id), e.g. to count orders above an amount."""
        if self.shop.lines is None:
            return self.orders(p, **where).set_index("order_id")["order_goods"]
        return self.sold(p, **where).groupby("order_id")["amount"].sum()

    def pct_change(self, new, old):
        """§1: giảm thì ra số âm."""
        return half_up(100 * (new - old) / old, 2)

    def share(self, part, whole):
        return half_up(100 * part / whole, 2)

    def daily(self, p, **where):
        """Revenue per day (Series indexed by date); days without a sale are absent."""
        if self.shop.lines is None:
            return self.orders(p, **where).groupby("o_date")["order_goods"].sum()
        return self.sold(p, **where).groupby("o_date")["amount"].sum()

    def best_day(self, p, **where):
        return self.daily(p, **where).idxmax().strftime("%Y-%m-%d")

    def best_month(self, periods):
        revs = [self.revenue(p) for p in periods]
        return periods[revs.index(max(revs))][0].strftime("%Y-%m")

    def rising(self, periods):
        """Doanh thu tăng liên tục: every period strictly above the one before."""
        revs = [self.revenue(p) for p in periods]
        return all(a < b for a, b in itertools.pairwise(revs))

    def gross_profit(self, p, **where):
        """§5: revenue minus quantity x the cost written on each sales line."""
        return half_up(self.sold(p, **where)["profit"].sum())

    def expenses(self, p):
        """Every amount in the expense book dated in the period."""
        df = self._role("expenses")
        return half_up(self._within(df, p, "exp_date")["exp_amount"].sum())

    def net_profit(self, p):
        return self.gross_profit(p) - self.expenses(p)

    # --- §3 items, rankings ---------------------------------------------------------------------------
    def qty(self, p, unit=None, **where):
        """Quantity sold in base units. unit='ly': only products whose base unit is 'ly' (§3)."""
        df = self.items(p, **where)
        if unit:
            df = df[df["base_unit"].map(key, na_action="ignore") == key(unit)]
        return tidy(df["base_qty"].sum())

    def top_products(self, p, k=1, by="base_qty", **where):
        """Names of the k top products by quantity in base units (bán chạy), by="amount" or by="profit".

        Ties go to the smaller code (§9).
        """
        total = self.items(p, **where).groupby("prod")[by].sum()
        return [self.shop.name_of(x) for x in sorted(total.index, key=lambda x: (-total[x], x))[:k]]

    def prod_orders(self, p, **where):
        """Number of different valid orders each product appears in (Series indexed by product)."""
        return self.items(p, **where).groupby("prod")["order_id"].nunique()

    def unsold(self, p):
        """§5 hàng ế: products sold in a valid order before the period and in none during it (names)."""
        start = pd.Timestamp(p[0])
        before = self.items((dt.date(1900, 1, 1), (start - pd.Timedelta(days=1)).date()))["prod"]
        return [self.shop.name_of(x) for x in sorted(set(before.dropna()) - set(self.items(p)["prod"]))]

    def price_raise_day(self, prod, price):
        """§5 hiệu ứng tăng giá: "YYYY-MM-DD" of the first day the base-unit price of prod rose to `price`.

        Read from the price history when the shop keeps one, else from the unit prices on sales lines.
        """
        pid = self.shop.find_prod(prod)
        hist = next(iter(self.shop.by_role.get("prices", [])), None)
        if hist is not None and {"new_price", "price_date"} <= set(hist.columns):
            h = hist.assign(prod=self.shop.ident(hist))
            h = h[h["prod"] == pid].sort_values("price_date")
            series = list(zip(h["price_date"], h["new_price"]))
        else:
            df = self.shop.lines
            df = df[(df["prod"] == pid) & (df["qty"] > 0)]
            daily = (df["price"] * df["qty"] / df["base_qty"]).groupby(df["date"]).agg(lambda s: s.mode().iat[0])
            series = list(daily.items())
        for (_, before), (day, now) in itertools.pairwise(series):
            if abs(now - price) < 0.5 and now > before:
                return as_ts(day).strftime("%Y-%m-%d")
        raise ValueError(f"no rise of {prod} to {price}")

    def price_on(self, prod, day):
        """§5 giá bán X ngày D: unit price for one base unit, before line discount, on lines of orders placed
        that day. Cancelled and returned orders count too."""
        df = self.shop.lines
        df = df[(df["prod"] == self.shop.find_prod(prod)) & (df["date"] == as_ts(day)) & (df["qty"] > 0)]
        return tidy((df["price"] * df["qty"] / df["base_qty"]).mode().iat[0])

    def _buyers(self, df):
        """Rows with a known customer. NoData when the shop never records who bought."""
        src = self.shop.lines if self.shop.lines is not None else self.shop.orders
        if src["cust"].isna().all():
            raise NoData("the shop does not record which customer bought")
        return df.dropna(subset=["cust"])

    def cust_revenue(self, p):
        """Revenue per customer (walk-in sales have no customer and are left out)."""
        return self._buyers(self.sold(p)).groupby("cust")["amount"].sum()

    def cust_orders(self, p):
        """Number of valid orders per customer."""
        return self._buyers(self.orders(p)).groupby("cust").size()

    def top_customers(self, p, k=1, by="revenue"):
        """Names of the k customers who spent most (by="revenue") or ordered most often (by="orders")."""
        return self._ranked_customers(self.cust_revenue(p) if by == "revenue" else self.cust_orders(p), k)

    def _ranked_customers(self, total, k):
        """Customer names, highest total first; ties by code (§9)."""
        src = self.shop.lines if self.shop.lines is not None else self.shop.orders
        by_name = "cust_name" in src and PLATE_NOTE not in self.shop.notes  # a vehicle is answered by its plate
        names = src.dropna(subset=["cust"]).groupby("cust")["cust_name"].first() if by_name else {}
        return [names.get(c, c) for c in sorted(total.index, key=lambda c: (-total[c], c))[:k]]

    def top_staff(self, p, k=1, by="revenue"):
        """§3 nhân viên bán giỏi nhất: by revenue; by="tickets": most tickets or orders, warranty tickets included
        (§6 sửa xe thợ làm nhiều nhất)."""
        if by != "revenue":
            n = self.orders(p).dropna(subset=["staff"]).groupby("staff").size()
            return sorted(n.index, key=lambda s: (-n[s], s))[:k]
        total = self.sold(p).groupby("staff")["amount"].sum()
        return sorted(total.index, key=lambda s: (-total[s], s))[:k]

    # --- §3 rankings added by the public-test rulebook --------------------------------------------------
    def loyal_customers(self, p, k=1):
        """Khách trung thành nhất: most different days with a counted order in the period (names)."""
        return self._ranked_customers(self._buyers(self.orders(p)).groupby("cust")["o_date"].nunique(), k)

    def big_spenders(self, p, k=1):
        """Khách sộp nhất: highest revenue per counted order, among customers with at least 3 such orders (names)."""
        per = self.order_values(p)
        cust = self.orders(p).drop_duplicates("order_id").set_index("order_id")["cust"]
        df = self._buyers(pd.DataFrame({"v": per, "cust": cust.reindex(per.index)}))
        g = df.groupby("cust")["v"].agg(["sum", "count"])
        g = g[g["count"] >= 3]
        return self._ranked_customers(g["sum"] / g["count"], k)

    def fickle_customers(self, p, k=1):
        """Khách hay đổi ý nhất: most cancelled orders placed in the period (names)."""
        return self._ranked_customers(self._buyers(self.orders(p, status=("cancel",))).groupby("cust").size(), k)

    def popular_products(self, p, k=1):
        """Mặt hàng được ưa chuộng nhất: in the most different counted orders (names; ties by code)."""
        n = self.prod_orders(p)
        return [self.shop.name_of(x) for x in sorted(n.index, key=lambda x: (-n[x], x))[:k]]

    def hardest_workers(self, p, k=1):
        """Nhân viên, thợ chăm chỉ nhất: most different days with at least one counted order (warranty tickets
        included) sold or done by the person."""
        df = self.orders(p)
        if "staff" not in df:
            raise NoData("the shop does not record who sold or did the work")
        n = df.dropna(subset=["staff"]).groupby("staff")["o_date"].nunique()
        return sorted(n.index, key=lambda s: (-n[s], s))[:k]

    def busiest_day(self, p):
        """Ngày đắt khách nhất: "YYYY-MM-DD" of the day with the most counted orders (ties: the earlier day)."""
        n = self.orders(p).groupby("o_date").size()
        return min(n.index, key=lambda d: (-n[d], d)).strftime("%Y-%m-%d")

    def peak_hour(self, p):
        """Giờ cao điểm: start hour h of the clock hour [h, h+1) with the most counted orders, by creation time."""
        df = self.orders(p)
        if "o_ts" not in df or df["o_ts"].isna().all():
            raise NoData("the shop does not record the time of an order")
        n = df.dropna(subset=["o_ts"]).groupby(df["o_ts"].dt.hour).size()
        return int(min(n.index, key=lambda h: (-n[h], h)))

    def weekday_avg(self, p, weekday, of="revenue"):
        """Trung bình mỗi ngày thứ X, weekday 0 = thứ Hai (T2) ... 5 = thứ Bảy (T7), 6 = Chủ nhật: revenue or counted orders (of="orders").

        Divided by how many such weekdays the period has up to today, days without orders included.
        """
        if weekday not in range(7):
            raise ValueError("weekday is 0 (thứ Hai) to 6 (Chủ nhật)")
        a, b = pd.Timestamp(p[0]), min(pd.Timestamp(p[1]), self.as_of)
        days = pd.date_range(a, b)
        n = int((days.weekday == weekday).sum())
        if of == "revenue":
            d = self.daily(p)
        else:
            d = self.orders(p).groupby("o_date").size()
        total = d[pd.DatetimeIndex(d.index).weekday == weekday].sum()
        return half_up(total / n) if of == "revenue" else half_up(total / n, 2)

    def main_supplier(self, p):
        """Nhà cung cấp chính: the supplier with the most purchase slips dated in the period (cancelled slips gone)."""
        df = self.bought(p)
        if "supplier" not in df:
            raise NoData("the shop does not record the supplier of a purchase")
        slip = "po_id" if "po_id" in df else "po_date"
        n = df.dropna(subset=["supplier"]).groupby("supplier")[slip].nunique()
        return min(n.index, key=lambda x: (-n[x], x))

    def charges(self, p, kind):
        """Tiền giảm voucher (kind="voucher"), tiền giảm nhờ dùng điểm ("points") or phí ship ("ship") of the counted
        orders placed in the period, as a positive amount."""
        df = self._have("lines")
        if "charge" in df:
            df = self._within(df[df["charge"] & df["o_status"].isin(VALID) & (df["st"] != "return")], p)
            pat = {"voucher": r"^(?:voucher|ma giam gia)", "points": r"^dung \d+ diem", "ship": r"^phi "}[kind]
            return half_up(df.loc[df["prod_name"].map(key).str.match(pat), "amount"].abs().sum())
        o = self._have("orders")
        col = {"ship": "ship", "voucher": "order_disc"}.get(kind)
        if col not in o:
            raise NoData(f"the shop does not record {kind}")
        return half_up(self.orders(p)[col].abs().sum())

    # --- §5 purchases and stock -----------------------------------------------------------------------
    def bought(self, p, prod=None):
        """Purchase lines of slips dated in the period (cancelled slips are already gone)."""
        df = self._within(self._have("purchases"), p, "po_date")
        return df if prod is None else df[df["prod"] == self.shop.find_prod(prod)]

    def purchase_total(self, p):
        """Tiền nhập hàng trong kỳ: total of the purchase slips dated in the period."""
        df = self.bought(p)
        if "po_total" in df:
            return half_up(df.drop_duplicates("po_id")["po_total"].sum())
        return half_up(df["po_amount"].sum())

    def avg_purchase_price(self, p, prod):
        """Giá nhập bình quân: weighted by quantity, per purchase unit."""
        df = self.bought(p, prod)
        return half_up((df["po_qty"] * df["po_price"]).sum() / df["po_qty"].sum())

    def purchased_qty(self, p, prod):
        """Quantity bought, in base units."""
        return tidy(self.bought(p, prod)["base_qty"].sum())

    def top_suppliers(self, p, k=1):
        """Suppliers with the most purchase money in the period."""
        df = self.bought(p)
        who = "supplier" if "supplier" in df else "supplier_id"
        total = (df.drop_duplicates("po_id").groupby(who)["po_total"] if "po_total" in df else df.groupby(who)["po_amount"]).sum()
        return sorted(total.index, key=lambda s: (-total[s], s))[:k]

    def stock_levels(self):
        """Stock at the end of today per product, in base units (Series indexed by product identity)."""
        ledger = self.shop.by_role.get("ledger")
        if ledger:  # stock card: the balance after the last movement up to today
            df = ledger[0].assign(prod=self.shop.ident(ledger[0]))
            df = df[df["led_ts"] < self.as_of + pd.Timedelta(days=1)].sort_values(["led_ts", "_row"])
            return df.groupby("prod")["stock_after"].last()
        end = self.shop.readme.get("data_end")
        if self.shop.stock is not None and not (end and self.P.as_of < end):
            return self.shop.stock.set_index("prod")["stock_qty"]  # recorded for the last day of data
        if self.shop.by_role.get("lots"):
            return self.lots().groupby("prod")["left"].sum()
        raise NoData("stock is only recorded for the last day of data")

    def lots(self):
        """Every lot received up to today: lot, prod, expiry, left = received - sold + returned (base units)."""
        lots = self._role("lots")
        lots = lots.assign(prod=self.shop.ident(lots))
        slips = self.shop.purchases if self.shop.purchases is not None else next(
            (f for f in self.shop.by_role.get("purchases", []) if "po_date" in f), None)  # fmt: skip
        if "po_date" not in lots and slips is not None and "po_id" in lots:
            lots = lots.merge(slips.drop_duplicates("po_id")[["po_id", "po_date"]], on="po_id", how="left")
        if "po_date" in lots:
            lots = lots[lots["po_date"] <= self.as_of]
        out = self._role("lot_sales").merge(self._have("orders")[["order_id", "o_date", "o_status"]], on="order_id")
        out = out[(out["o_date"] <= self.as_of) & (out["o_status"] != "cancel")]
        used = (out["lot_qty"] - out.get("lot_ret_qty", 0)).groupby(out["lot"]).sum()
        return lots.assign(left=lots["lot_in_qty"] - lots["lot"].map(used).fillna(0))

    def nearest_expiry(self, prod):
        """§6 lô gần hết hạn nhất: among lots still in stock and not yet expired today, the earliest expiry date."""
        d = self.lots()
        d = d[(d["prod"] == self.shop.find_prod(prod)) & (d["left"] > 0) & (d["expiry"] >= self.as_of)]
        return d["expiry"].min().strftime("%Y-%m-%d")

    def expiring_lots(self, p):
        """§6 lots still in stock whose expiry falls in the period, with `value` = what is left x the lot's own
        purchase price per base unit. len() counts the lots, ["value"].sum() is the tiền vốn."""
        d = self.lots()
        d = d[(d["left"] > 0) & (d["expiry"] >= pd.Timestamp(p[0])) & (d["expiry"] <= pd.Timestamp(p[1]))]
        if "po_price" in d:  # the lot list itself prices a base unit
            return d.assign(value=d["left"] * d["po_price"])
        b = self.shop.purchases.drop_duplicates("lot").set_index("lot")  # else: the purchase line of that lot
        return d.assign(value=d["left"] * d["lot"].map(b["po_amount"] / b["base_qty"]))

    def expiring(self, p):
        """Products with stock in lots whose expiry falls in the period: earliest expiry first, then code (§9)."""
        d = self.expiring_lots(p)
        first = d.groupby("prod")["expiry"].min()
        return [self.shop.name_of(x) for x in sorted(first.index, key=lambda x: (first[x], x))]

    def out_of_stock(self):
        """Products whose stock at the end of today is 0 (names)."""
        s = self.stock_levels()
        return [self.shop.name_of(x) for x in sorted(s[s <= 0].index)]

    def n_deliveries(self, p):
        """§6 VLXD số đợt giao: every delivery round of every order with its delivery date in the period."""
        d = self._within(self._role("deliveries"), p, "deliv_date")
        return len(d.drop_duplicates(["order_id", "deliv_no"]))

    def stock(self, prod):
        """Stock of one product at the end of today, in base units: "còn bao nhiêu viên / gói / chai"."""
        return tidy(self.stock_levels()[self.shop.find_prod(prod)])

    def stock_split(self, prod, unit):
        """§5 tồn theo đơn vị lớn, only for "mấy <unit> chẵn, mấy ... lẻ": [whole big units, leftover base units]."""
        f = self.shop.factor[self.shop.find_prod(prod), key(unit)]
        q = self.stock(prod)
        return [int(q // f), tidy(q % f)]

    def stock_value(self):
        """§5 giá trị hàng tồn kho: stock x catalogue cost per base unit, products in stock only."""
        cost = self.shop.products.set_index("prod")["prod_cost"]
        s = self.stock_levels()
        return half_up((s[s > 0] * cost.reindex(s[s > 0].index)).sum())

    # --- §4 debt --------------------------------------------------------------------------------------
    def debts(self, day=None, cust=None):
        """Orders still owing at the end of `day` (default today), with column `left` = unpaid part of the
        order total. Cancelled, COD and marketplace orders are never debt; returned orders can be."""
        at = as_ts(day) if day else self.as_of
        o = self._have("orders")
        if "order_total" not in o and "debt" not in o:
            side = self._side_debts(at)  # §6 điện thoại: a debt book and an instalment book are the shop's debt
            if side is None:
                raise NoData("the shop does not record what customers owe")
            return side if cust is None else side[side["cust"] == cust_key(cust)]
        o = o[(o["o_date"] <= at) & (o["o_status"] != "cancel")]
        o = o[o["pay"] != "cod"] if "pay" in o else o
        o = o[o["chan"] != "ecom"] if "chan" in o else o
        if cust is not None:
            o = self._filter(o, {"cust": cust}) if cust in set(o["cust"]) else o[o["cust_name"].map(cust_key, na_action="ignore") == cust_key(cust)]
        pays = self.shop.payments
        if pays is not None:
            paid = pays[pays["pay_ts"] < at + pd.Timedelta(days=1)].groupby("order_id")["pay_amount"].sum()
            left = o["order_total"] - o["order_id"].map(paid).fillna(0)
        elif "debt" in o and at.date() >= (self.shop.readme.get("data_end") or at.date()):
            left = o["debt"]
        else:
            raise NoData("no payment history: debt is only known for the last day of data")
        o = o.assign(left=left, age=(at - o["o_date"]).dt.days)
        return o[o["left"] > 0.5]

    def debt_total(self, day=None, cust=None):
        return half_up(self.debts(day, cust)["left"].sum())

    def debt_by_customer(self, day=None):
        """Debt per customer; walk-in orders have no customer row (§4)."""
        return self.debts(day).dropna(subset=["cust"]).groupby("cust")["left"].sum()

    def n_debtors(self, day=None):
        return len(self.debt_by_customer(day))

    def top_debtors(self, k, day=None):
        """Table of the k customers owing most."""
        total = self.debt_by_customer(day)
        o = self.shop.orders
        names = o.dropna(subset=["cust"]).groupby("cust")["cust_name"].first() if "cust_name" in o else {}
        rows = [[names.get(c, c), half_up(total[c])] for c in sorted(total.index, key=lambda c: (-total[c], c))[:k]]
        return {"columns": ["khach_hang", "so_tien_no"], "rows": rows}

    def debt_aging(self, bounds, day=None):
        """Debt by age in days since the order date: bounds=[7, 15] -> [0-7, 8-15, over 15]."""
        d = self.debts(day)
        edges = [-1, *bounds, 10**9]
        return [half_up(d.loc[(d["age"] > lo) & (d["age"] <= hi), "left"].sum()) for lo, hi in itertools.pairwise(edges)]

    def _credit_orders(self, cust=None):
        o = self._have("orders")
        o = o[(o["pay"] == "debt") & (o["o_status"] != "cancel")]
        if cust is not None:
            o = o[(o["cust"] == cust) | (o["cust_name"].map(cust_key, na_action="ignore") == cust_key(cust))]
        return o

    def new_debt(self, p, cust=None):
        """§4 nợ thêm: for credit orders placed in the period, the part of the total not paid on the order day."""
        o = self._within(self._credit_orders(cust), p)
        pays = self._have("payments").merge(o[["order_id", "o_date"]], on="order_id")
        same_day = pays[pays["pay_ts"].dt.normalize() == pays["o_date"]].groupby("order_id")["pay_amount"].sum()
        return half_up((o["order_total"] - o["order_id"].map(same_day).fillna(0)).sum())

    def repaid(self, p, cust=None):
        """§4 trả nợ / thu nợ: payments dated in the period and after the order day, on any credit order."""
        o = self._credit_orders(cust)
        pays = self._have("payments").merge(o[["order_id", "o_date"]], on="order_id")
        pays = pays[pays["pay_ts"].dt.normalize() > pays["o_date"]]
        return half_up(self._within(pays, p, "pay_ts")["pay_amount"].sum())

    # --- §6 lines of business of the public-test rulebook -----------------------------------------------
    # Side books are found by what their sheet is called ("Trả góp", "Bảo hành", "ThuMayCu") and their columns by
    # what they are called ("Giá thu", "Số kỳ"): the rule names the record, the shop names the sheet.
    def _book(self, *words):
        """(name, frame) of the side table whose sheet name (accents, case, spaces ignored) contains a word, else NoData."""
        tables = self.shop.views or {n: t.df for n, t in self.shop.tables.items()}
        for w in words:
            for name, df in tables.items():
                if w in letters(name.rsplit(".", 1)[-1]):
                    return name, df
        raise NoData(f"the shop keeps no {words[0]} book")

    def trade_ins(self, p):
        """§6 điện thoại, thu máy cũ: one row per counted order placed in the period that took an old phone in, with
        `value` = the money it took off the order. ["value"].sum() is tiền thu máy cũ; len() the orders."""
        sold = self.sold(p)
        if col := find_col(sold, part=("thumaycu", "maycu")):
            v = sold.groupby("order_id")[col].first()
        else:
            name, book = self._book("thumaycu", "maycu", "thucu")
            link = next((c for t, c, to in self.shop.links if t == name and to == "order"), None)
            price = find_col(book, part=("giathu", "tienthu", "gia"))
            if link is None or price is None:
                raise NoData("the trade-in book names no sales order")
            d = self.with_order(book, link)
            v = d[d["order_id"].isin(set(sold["order_id"]))].groupby("order_id")[price].sum()
        v = v[v.fillna(0) > 0]
        return pd.DataFrame({"order_id": v.index, "value": v.to_numpy()})

    def customer_pays(self, p):
        """§6 điện thoại, khách phải trả: per counted order placed in the period, its goods minus the order's discount
        (bớt) minus the old phone taken in, any part still owed included. Series by order: .sum(), .mean()."""
        sold = self.sold(p)
        goods = sold.groupby("order_id")["amount"].sum()
        dcol = "order_disc" if "order_disc" in sold else find_col(sold, exact=("bot", "giam", "giamgia", "chietkhau", "ck"))
        disc = sold.groupby("order_id")[dcol].first().fillna(0) if dcol else 0
        try:
            trade = self.trade_ins(p).set_index("order_id")["value"]
        except NoData:
            trade = pd.Series(dtype=float)
        return goods - disc - trade.reindex(goods.index).fillna(0)

    def phones(self, p, **where):
        """§6 điện thoại: counted sales lines of phones (not accessories) placed in the period, with `dong_may` (the
        name before "…GB") and `hang` (first word; iPhone is Apple). Count phones with ["base_qty"].sum()."""
        df = self.items(p, **where)
        df = df[is_phone(df)]
        return df.assign(dong_may=df["prod_name"].map(model_line), hang=df["prod_name"].map(brand_of))

    def top_lines(self, p, k=1, by="base_qty"):
        """§6 điện thoại: the k phone lines (dòng máy) selling most by quantity, or by="amount"."""
        total = self.phones(p).groupby("dong_may")[by].sum()
        return sorted(total.index, key=lambda x: (-total[x], x))[:k]

    def warranty_rate(self, p):
        """§6 bảo hành, tỉ lệ: repair shop = warranty tickets / paid tickets of the period; phone shop = warranty
        visits received in the period / phones sold in it (counted orders)."""
        if self._warranty_book() is not None:
            return half_up(100 * len(self.warranty_visits(p)) / self.phones(p)["base_qty"].sum(), 2)
        t = self.tickets(p)
        return half_up(100 * t["warranty"].sum() / t["paid"].sum(), 2)

    def _warranty_book(self):
        try:
            return self._book("baohanh")
        except NoData:
            return None

    def warranty_visits(self, p=None, sold=None):
        """§6 điện thoại: rows of the warranty book received in the period (default: everything up to today; one row =
        one visit), `dong_may` = its phone. sold=<period>: only phones sold in that period (matched by IMEI)."""
        _, book = self._book("baohanh")
        dates = [c for c in book.columns if pd.api.types.is_datetime64_any_dtype(book[c])]
        got = find_col(book, part=("ngaynhan",)) or dates[0]
        d = self.rows(book, got, p) if p is not None else book[book[got] < self.as_of + pd.Timedelta(days=1)]
        if sold is not None:
            imei, lines = find_col(d, part=("imei", "serial")), self.sold(sold)
            if imei is None or imei not in lines:
                raise NoData("the warranty book and the sales book share no IMEI column")
            d = d[d[imei].isin(set(lines[imei].dropna()))]
        mcol = find_col(d, exact=("may", "dongmay", "tenmay"))
        return d.assign(dong_may=d[mcol].map(model_line)) if mcol else d

    def installments(self, day=None):
        """§6 điện thoại, trả góp at the end of `day` (default today), one row per contract bought by then: cust_name,
        total (all instalments), paid (instalments paid by then), left = total - paid (còn chưa góp, kỳ chưa tới hạn
        included), due (instalments due on or before the day), overdue (instalments due before the day and not fully
        paid), overdue_left (money of those)."""
        at = as_ts(day) if day else self.as_of
        _, book = self._book("tragop", "gop")
        dates = [c for c in book.columns if pd.api.types.is_datetime64_any_dtype(book[c])]
        bought, paid_on = dates[0], (find_col(book, part=("ngaygop", "ngaytra")) or dates[-1])
        n_col, per_col = find_col(book, part=("soky",)), find_col(book, part=("gopthang", "moithang", "moiky"))
        day_col, paid_col = find_col(book, part=("hangop", "han")), find_col(book, part=("dagop", "datra"))
        name_col = find_col(book, exact=("khach", "tenkhach", "khachhang"))
        if None in (n_col, per_col, paid_col):
            raise NoData("the instalment book does not say the instalments")
        entry = "_entry" if "_entry" in book else bought
        head = book.groupby(entry).first()
        head = head[head[bought] <= at]
        pays = book[book[paid_on] <= at].groupby(entry)[paid_col].sum()
        rows = []
        for e, h in head.iterrows():
            n, per = int(h[n_col]), float(h[per_col])
            hday = int(h[day_col]) if day_col and pd.notna(h[day_col]) else h[bought].day
            dues = [_month_day(h[bought], i + 1, hday) for i in range(n)]
            paid = float(pays.get(e, 0))
            before = sum(d < at.normalize() for d in dues)
            rows.append({"cust_name": h[name_col] if name_col else None, "o_date": h[bought], "total": n * per,
                         "paid": paid, "left": n * per - paid, "due": sum(d <= at.normalize() for d in dues),
                         "overdue": max(0, before - int(paid // per + 1e-9)) if per else 0,
                         "overdue_left": max(0.0, before * per - paid)})  # fmt: skip
        return pd.DataFrame(rows, columns=["cust_name", "o_date", "total", "paid", "left", "due", "overdue", "overdue_left"])

    def _side_debts(self, at):
        """Debt kept in side books (a debt book with a payment per row, an instalment book), as debts() rows."""
        out = []
        try:
            _, book = self._book("sono", "congno")
            dates = [c for c in book.columns if pd.api.types.is_datetime64_any_dtype(book[c])]
            owed, paid = find_col(book, exact=("no", "conno", "tienno")), find_col(book, part=("datra",))
            name = find_col(book, exact=("khach", "tenkhach", "khachhang"))
            entry = "_entry" if "_entry" in book else dates[0]
            head = book.groupby(entry).first()
            head = head[head[dates[0]] <= at]
            pays = book[book[dates[-1]] <= at].groupby(entry)[paid].sum() if paid and len(dates) > 1 else pd.Series(dtype=float)
            out.append(pd.DataFrame({"cust_name": head[name], "o_date": head[dates[0]],
                                     "left": head[owed] - pays.reindex(head.index).fillna(0)}))  # fmt: skip
        except (NoData, KeyError, IndexError, TypeError):
            pass
        try:
            inst = self.installments(at)
            if len(inst):
                out.append(inst[["cust_name", "o_date", "left"]])
        except NoData:
            pass
        if not out:
            return None
        d = pd.concat(out, ignore_index=True)
        d = d.assign(cust=d["cust_name"].map(lambda x: cust_key(x) if isinstance(x, str) else None), age=(at - d["o_date"]).dt.days)
        return d[d["left"] > 0.5]

    # --- §6 mẹ và bé ----------------------------------------------------------------------------------
    def new_members(self, p):
        """§6 mẹ và bé: customers whose registration date (Ngày đăng ký) lies in the period, buyers or not (count)."""
        cust = self._role("customers")
        col = find_col(cust, part=("dangky", "ngaytao", "thamgia")) or next(
            (c for c in cust.columns if pd.api.types.is_datetime64_any_dtype(cust[c])), None)  # fmt: skip
        if col is None:
            raise NoData("the customer list records no registration date")
        return len(self.rows(cust, col, p))

    # --- §6 sửa xe ------------------------------------------------------------------------------------
    def tickets(self, p, **where):
        """§6 sửa xe: every ticket (row of the repair book) of the period, warranty tickets included, with `warranty`
        (the note says bảo hành), `paid` (not warranty), `plate`, `dong_xe` (the vehicle line, spellings merged)."""
        df = self.orders(p, **where)
        warranty = df["note"].map(lambda x: "bao hanh" in fold(x) if isinstance(x, str) else False) if "note" in df else False
        df = df.assign(warranty=warranty | (df["order_total"].fillna(0) == 0) if "order_total" in df else warranty)
        df = df.assign(paid=~df["warranty"].astype(bool), plate=df["cust"])
        if "cust_name" in df:
            lines = vehicle_lines(self.shop)
            df = df.assign(dong_xe=df["cust_name"].map(lambda x: lines.get(_vehicle_name(x)) if isinstance(x, str) else None))
        return df

    def avg_ticket(self, p, **where):
        """§6 sửa xe, tiền trung bình một phiếu: the period's money / its paid tickets (warranty tickets left out)."""
        t = self.tickets(p, **where)
        return half_up(t.loc[t["paid"], "order_total"].sum() / t["paid"].sum())

    def jobs(self, p, job, **where):
        """§6 sửa xe, lượt làm một việc: paid tickets whose Làm gì names the job (count). Rulebook: plain "nhớt" is
        engine oil, not nhớt láp; plain "ruột" is a new tube, not a patch (vá); sửa điện is not bulbs or batteries."""
        t = self.tickets(p, **where)
        col = find_col(t, part=("lamgi", "congviec", "noidung", "viec")) or "note"
        return int(t[t["paid"]][col].map(lambda x: does_job(x, job)).sum())

    def vehicles(self, p, line=None, **where):
        """§6 sửa xe, số chiếc xe: different plates that came in the period (warranty visits too), of one line if given."""
        t = self.tickets(p, **where)
        if line is not None:
            t = t[t["dong_xe"] == vehicle_lines(self.shop).get(_vehicle_name(line), line)]
        return int(t["plate"].nunique())

    def regulars(self, p, visits=2):
        """§6 sửa xe, xe khách quen: plates with at least `visits` paid tickets in the period (list; len() counts)."""
        t = self.tickets(p)
        n = t[t["paid"]].groupby("plate").size()
        return sorted(n[n >= visits].index)

    def top_vehicle_lines(self, p, k=1, by="money"):
        """§6 sửa xe: the k vehicle lines with the most money (by="money") or tickets (by="tickets") in the period."""
        t = self.tickets(p)
        total = t.groupby("dong_xe")["order_total"].sum() if by == "money" else t.groupby("dong_xe").size()
        return sorted(total.index, key=lambda x: (-total[x], x))[:k]

    # --- §6 vật liệu xây dựng ---------------------------------------------------------------------------
    def credit_terms(self, day=None):
        """Credit sales with a written due date (sổ hạn nợ) of orders placed by the end of `day` (default today), not
        cancelled: order_id, o_date, o_status, due_date (one row per order; len() counts them)."""
        at = as_ts(day) if day else self.as_of
        t = self._role("terms")
        o = self._have("orders").drop_duplicates("order_id")[["order_id", "o_date", "o_status"]]
        d = t.drop(columns=[c for c in ("o_date", "o_status") if c in t]).merge(o, on="order_id", how="inner")
        return d[(d["o_date"] <= at) & (d["o_status"] != "cancel")].drop_duplicates("order_id")

    def delivered(self, p, prod=None):
        """§6 VLXD: delivery rows up to the end of today of the orders placed in the period (cancelled orders out;
        rounds of an order returned later still count), of one product if given: order_id, deliv_no, deliv_date,
        prod_name, base_qty (already in base_unit, the unit the product is counted in: never divide it again)."""
        d = self._role("deliveries")
        d = d[d["deliv_date"] < self.as_of + pd.Timedelta(days=1)]
        if prod is not None:
            d = d[d["prod_name"].map(key, na_action="ignore") == key(prod)]
        o = self._within(self._have("orders").drop_duplicates("order_id"), p)
        return d[d["order_id"].isin(set(o.loc[o["o_status"] != "cancel", "order_id"]))]


def letters(s):
    """Only the letters and digits of a name, accents and case dropped: "Trả góp" -> "tragop"."""
    return re.sub(r"[^a-z0-9]", "", fold(str(s)))


def find_col(df, exact=(), part=()):
    """First column whose letters equal one of `exact`, else contain one of `part` (internal columns never match)."""
    cols = [c for c in df.columns if not str(c).startswith("_")]
    for c in cols:
        if letters(c) in exact:
            return c
    for w in part:
        for c in cols:
            if w in letters(c):
                return c
    return None


def _month_day(start, months, day):
    """The day-th of the month `months` after start's month (the month's last day when it is shorter)."""
    y, mo = divmod(start.month - 1 + months, 12)
    first = pd.Timestamp(start.year + y, mo + 1, 1)
    return first + pd.Timedelta(days=min(day, first.days_in_month) - 1)


def model_line(name):
    """Dòng máy: the name before its capacity "…GB" ("iPhone 16 128GB Đen" -> "iPhone 16"); no capacity: the name."""
    if not isinstance(name, str):
        return name
    m = re.search(r"^(.*?)\s*\d+\s*(?:GB|TB)\b", name, re.IGNORECASE)
    return (m[1] if m else name).strip()


def brand_of(name):
    """Hãng: the first word of the name; an iPhone is an Apple."""
    if not isinstance(name, str) or not name.split():
        return name
    w = name.split()[0]
    return "Apple" if fold(w).startswith("iphone") else w


def is_phone(df):
    """Phones among sales lines: they carry an IMEI; else their kind of goods is no accessory; else "…GB" names."""
    if col := find_col(df, exact=("imei",)):
        return df[col].notna()
    if "group" in df:
        return ~df["group"].map(lambda g: "phu kien" in fold(str(g)))
    return df["prod_name"].str.contains(r"\d+\s*GB", case=False, na=False)


def _vehicle_name(cell):
    """The vehicle line written in a "dòng xe + biển số" cell, case folded, the plate and Greek letters gone."""
    s = fold(str(cell).replace("α", " alpha"))
    return " ".join(re.sub(r"\s*\d{2}\s*[a-z]\w?\s*-?\s*[\d.]{4,7}\s*$", "", s).split())


def vehicle_lines(shop):
    """{written line -> one name per vehicle line}: spellings that differ in case or spacing, an abbreviation (AB ->
    Air Blade, Ex -> Exciter) or a dropped word (Alpha -> Wave Alpha, Vision -> Vision 110) are one line."""
    if getattr(shop, "_vehicle_lines", None) is None:
        src = shop.orders if shop.orders is not None else shop.lines
        cells = src["cust_name"].dropna() if src is not None and "cust_name" in src else pd.Series(dtype=object)
        spelled = {}
        for cell in cells:
            plain = re.sub(r"\s*\S*\d\S*\s*-?\s*[\d.]{4,7}\s*$", "", str(cell)).strip()
            spelled.setdefault(_vehicle_name(cell), collections.Counter())[plain] += 1
        names = sorted(spelled, key=lambda n: (-len(n.split()), -len(n), n))

        def same(a, b):  # is the shorter spelling a the line b?
            ta, tb, ca, cb = a.split(), b.split(), a.replace(" ", ""), b.replace(" ", "")
            return (ca == cb or (set(ta) < set(tb) and not ta[0].isdigit()) or (len(tb) > 1 and ca == "".join(w[0] for w in tb))
                    or (len(ta) == 1 and len(tb) == 1 and len(ca) >= 2 and cb.startswith(ca)))  # fmt: skip

        root = {}
        for i, a in enumerate(names):
            root[a] = next((root[b] for b in names[:i] if same(a, b)), a)
        shown = {}
        for r in set(root.values()):  # a line is shown as the commonest capitalised spelling of its fullest name
            pick = spelled[r]
            shown[r] = max(pick, key=lambda w: (w.isascii(), w != w.lower(), pick[w]))
        shop._vehicle_lines = {n: shown[r] for n, r in root.items()}
    return shop._vehicle_lines


# Job words in a repair book: abbreviations spelled out, so the rulebook's readings apply to them too.
JOB_WORDS = {"bd": "bao duong", "bdtq": "bao duong", "vs": "ve sinh", "nsd": "nhong sen dia", "bh": "bao hanh",
             "cu": "cu", "binh": "ac quy", "bo": "bo thang"}  # fmt: skip
OIL = ("nhot", "castrol", "motul", "shell")


def does_job(cell, job):
    """True when a ticket's job cell names the job, read by the rulebook (§6 sửa xe)."""
    if not isinstance(cell, str):
        return False
    want = " ".join(JOB_WORDS.get(w, w) for w in re.sub(r"\b(?:thay|lam|sua|cac|viec)\b", " ", fold(job)).split())
    want = want.replace("ma phanh", "bo thang").replace("bu gi", "bugi").replace("cu roa", "curoa")
    for task in re.split(r"[,+;/]", fold(cell)):  # not on "và": unaccented it is "vá" (a patch)
        t = " ".join(JOB_WORDS.get(w, w) for w in task.split())
        t = t.replace("ma phanh", "bo thang").replace("bu gi", "bugi").replace("cu roa", "curoa").replace("ac quy gs", "ac quy")
        if "bao hanh" in t:
            continue
        words = t.split()
        if "nhot" in want.split() and "lap" not in want:  # plain oil is engine oil, never gear oil (láp)
            hit = any(w in words for w in OIL) and "lap" not in words and "hop" not in words
        elif "lap" in want.split():
            hit = "lap" in words or "hop so" in t
        elif "va" in want.split():  # vá, vá xe, vá ruột: a patch
            hit = "va" in words
        elif "ruot" in want.split():  # plain tube is a new tube, not a patch
            hit = "ruot" in words and "va" not in words
        elif "dien" in want.split():  # sửa điện: not bulbs, not batteries
            hit = "dien" in words and "den" not in words and "ac quy" not in t
        else:
            hit = bool(want.split()) and all(w in words for w in want.split())
        if hit:
            return True
    return False


def attr_is(value, want):
    """A catalogue attribute cell matches: "Size XL" is size XL, "Số 2" is số 2 (a blank cell is no size, no số)."""
    if not isinstance(value, str) or not value.strip():
        return False
    v, w = key(value), key(str(want))
    return v == w or v.split()[-1] == w.split()[-1] and (len(w.split()) == 1 or v == w)
