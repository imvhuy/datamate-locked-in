"""Rulebook metrics as code. One Metrics object per question:

    m = Metrics(shop, as_of, lunar)      # m.P builds periods
    m.revenue(m.P.month(2025, 7))
    m.revenue(m.P.last_week(), prod="Sting dâu 330ml")
    m.cancel_rate(m.P.this_month())

Every method takes a period (start, end). Records dated after as_of have not happened yet and are ignored.

Filters (`**where`) accepted wherever a method says so:
    prod="name or code"   cust="name or code"   group="nhóm hàng"   cust_group="nhóm khách"   staff="NV001"
    shift="sáng" | "chiều"   size="M"   pay="cash|transfer|ewallet|cod|debt"   chan="store|phone|zalo|ecom"
"""
import datetime as dt
import itertools
import math

import pandas as pd

from src.mapper import FIELDS
from src.period import Periods
from src.shop import VALID, key


class NoData(Exception):
    """The released data cannot answer this (rulebook §7): the answer is `unanswerable`, never 0."""


# Columns a shop view may lack because the shop never recorded them; a KeyError on anything else is a bug.
CANON = set(FIELDS) | {"cust", "prod", "st", "o_status", "o_date", "o_ts", "pay", "chan", "is_item", "profit", "pkey"}


def is_no_data(e):
    """True when an error means "unanswerable": NoData, or a canonical column the shop does not have."""
    return isinstance(e, NoData) or (isinstance(e, KeyError) and bool(e.args) and e.args[0] in CANON)


def half_up(x, digits=0):
    """Rulebook rounding: money to the đồng with halves up, percents and averages to 2 decimals."""
    q = 10**digits
    v = math.floor(x * q + 0.5 + 1e-9) / q
    return int(v) if digits == 0 else v


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
        a, b = pd.Timestamp(p[0]), min(pd.Timestamp(p[1]), self.as_of)
        return df[(df[col] >= a) & (df[col] < b + pd.Timedelta(days=1))]

    def _filter(self, df, where):
        for col, want in where.items():
            if col == "prod":
                df = df[df["prod"] == self.shop.find_prod(want)]
            elif col == "cust":  # a code matches as is, a name through its spelling-insensitive key
                df = df[(df[col] == want) | (df[col] == key(want))]
            elif col == "staff":  # a code, or a name looked up in the staff list
                names = next(iter(self.shop.by_role.get("staff", [])), None)
                if names is not None and {"staff", "staff_name"} <= set(names.columns) and want not in set(df["staff"]):
                    want = dict(zip(names["staff_name"].map(key), names["staff"])).get(key(want), want)
                df = df[df["staff"] == want]
            elif col == "shift":  # §2: ca sáng 06:00-13:59, ca chiều 14:00-21:59, by order creation time
                lo, hi = (6, 14) if key(want) == "sang" else (14, 22)
                df = df[df["o_ts"].dt.hour.between(lo, hi - 1)]
            elif col == "size":  # §6 cà phê: the name ends with "(M)" / "(L)", any letter case
                df = df[df["prod_name"].str.lower().str.endswith(f"({want.lower()})", na=False)]
            elif col in ("pay", "chan") or isinstance(want, bool):  # rx=True / rx=False
                df = df[df[col] == want]
            else:
                df = df[df[col].map(key, na_action="ignore") == key(want)]
        return df

    def sold(self, p, **where):
        """Sales lines of orders that count as revenue (§1: not cancelled, no return) placed in the period."""
        df = self._have("lines")
        return self._filter(self._within(df[df["o_status"].isin(VALID) & (df["st"] != "return")], p), where)

    def items(self, p, **where):
        """sold() without rows that are not goods (§3: the 'Phí phục vụ' row)."""
        df = self.sold(p, **where)
        return df[df["is_item"]] if "is_item" in df else df

    def orders(self, p, status=VALID, **where):
        """Orders placed in the period with the given statuses (status=None: every status)."""
        df = self._filter(self._within(self._have("orders"), p), where)
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
                return pd.Timestamp(day).strftime("%Y-%m-%d")
        raise ValueError(f"no rise of {prod} to {price}")

    def price_on(self, prod, day):
        """§5 giá bán X ngày D: unit price for one base unit, before line discount, on lines of orders placed
        that day. Cancelled and returned orders count too."""
        df = self.shop.lines
        df = df[(df["prod"] == self.shop.find_prod(prod)) & (df["date"] == pd.Timestamp(day)) & (df["qty"] > 0)]
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
        total = self.cust_revenue(p) if by == "revenue" else self.cust_orders(p)
        src = self.shop.lines if self.shop.lines is not None else self.shop.orders
        names = src.dropna(subset=["cust"]).groupby("cust")["cust_name"].first() if "cust_name" in src else {}
        return [names.get(c, c) for c in sorted(total.index, key=lambda c: (-total[c], c))[:k]]

    def top_staff(self, p, k=1):
        """§3 nhân viên bán giỏi nhất: by revenue."""
        total = self.sold(p).groupby("staff")["amount"].sum()
        return sorted(total.index, key=lambda s: (-total[s], s))[:k]

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
        at = pd.Timestamp(day) if day else self.as_of
        o = self._have("orders")
        if "order_total" not in o and "debt" not in o:
            raise NoData("the shop does not record what customers owe")
        o = o[(o["o_date"] <= at) & (o["o_status"] != "cancel")]
        o = o[o["pay"] != "cod"] if "pay" in o else o
        o = o[o["chan"] != "ecom"] if "chan" in o else o
        if cust is not None:
            o = self._filter(o, {"cust": cust}) if cust in set(o["cust"]) else o[o["cust_name"].map(key, na_action="ignore") == key(cust)]
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
            o = o[(o["cust"] == cust) | (o["cust_name"].map(key, na_action="ignore") == key(cust))]
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
