"""Time periods exactly as rulebook §2 defines them. A period is (start, end): two dates, both included.

    P = Periods(as_of, lunar)      # as_of = "hôm nay" of the question
    P.month(2025, 7)  P.this_month()  P.last_week()  P.last_days(7)  P.tet(2026)

Records dated after as_of "have not happened yet": the metric layer drops them, periods here are not clipped
except where the rulebook says "đến hết hôm nay" (this_month, this_week, ytd).
"""
import calendar
import csv
import datetime as dt

DAY = dt.timedelta(days=1)


def read_lunar(path):
    """calendar.csv -> {(lunar_year, lunar_month, lunar_day): solar date}. Leap months are skipped."""
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["lunar_leap"].strip().lower() != "true":
                out[int(r["lunar_year"]), int(r["lunar_month"]), int(r["lunar_day"])] = dt.date.fromisoformat(r["date"])
    return out


class Periods:
    def __init__(self, as_of, lunar=None):
        self.as_of = dt.date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        self.lunar = lunar or {}

    # days
    def day(self, d):
        d = dt.date.fromisoformat(d) if isinstance(d, str) else d
        return d, d

    def today(self):
        return self.day(self.as_of)

    def yesterday(self):
        return self.day(self.as_of - DAY)

    def last_days(self, n):
        """N ngày gần nhất: n consecutive days ending today."""
        return self.as_of - (n - 1) * DAY, self.as_of

    def between(self, a, b):
        return self.day(a)[0], self.day(b)[0]

    def days_from(self, d, n):
        """N ngày kể từ ngày D: D itself and the n-1 days after it."""
        d = self.day(d)[0]
        return d, d + (n - 1) * DAY

    def days_before(self, d, n):
        """N ngày ngay trước ngày D: the n days ending the day before D."""
        d = self.day(d)[0]
        return d - n * DAY, d - DAY

    # weeks start on Monday
    def this_week(self):
        return self.as_of - self.as_of.weekday() * DAY, self.as_of

    def last_week(self):
        mon = self.as_of - (self.as_of.weekday() + 7) * DAY
        return mon, mon + 6 * DAY

    # months
    def month(self, y, m):
        return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])

    def month_of(self, m):
        """Tháng M không ghi năm: this year unless that month starts after today, then last year."""
        y = self.as_of.year
        return self.month(y if dt.date(y, m, 1) <= self.as_of else y - 1, m)

    def this_month(self):
        return self.as_of.replace(day=1), self.as_of

    def last_month(self):
        end = self.as_of.replace(day=1) - DAY
        return end.replace(day=1), end

    def same_days_last_month(self):
        """Cùng kỳ tháng trước: day 1 to the same day number, or to the month's end if it is shorter."""
        start, end = self.last_month()
        return start, min(end, start.replace(day=min(self.as_of.day, end.day)))

    # quarters, years
    def quarter(self, y, q):
        return dt.date(y, 3 * q - 2, 1), self.month(y, 3 * q)[1]

    def quarter_of(self, q):
        y = self.as_of.year
        return self.quarter(y if dt.date(y, 3 * q - 2, 1) <= self.as_of else y - 1, q)

    def year(self, y):
        return dt.date(y, 1, 1), dt.date(y, 12, 31)

    def ytd(self):
        return dt.date(self.as_of.year, 1, 1), self.as_of

    # Tết: 23 tháng Chạp of lunar year y-1 to mùng 10 tháng Giêng of lunar year y
    def tet(self, y):
        return self.lunar[y - 1, 12, 23], self.lunar[y, 1, 10]

    def before_tet(self, y):
        """So với trước Tết: the same number of days right before 23 tháng Chạp."""
        a, b = self.tet(y)
        return a - ((b - a).days + 1) * DAY, a - DAY

    def prev(self, p):
        """Kỳ trước of a period: the previous month / quarter / year if p is a whole one, else the same length just before."""
        a, b = p
        if a.day == 1 and b == self.month(b.year, b.month)[1]:
            months = (b.year - a.year) * 12 + b.month - a.month + 1
            if months in (1, 3, 12):
                end = a - DAY
                start = end.replace(day=1)
                for _ in range(months - 1):
                    start = (start - DAY).replace(day=1)
                return start, end
        return a - ((b - a).days + 1) * DAY, a - DAY
