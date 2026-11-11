import datetime as dt

from src.period import Periods, read_lunar

D = dt.date
P = Periods("2026-03-04")  # a Wednesday


def test_days_weeks():
    assert P.yesterday() == (D(2026, 3, 3), D(2026, 3, 3))
    assert P.last_days(7) == (D(2026, 2, 26), D(2026, 3, 4))
    assert P.this_week() == (D(2026, 3, 2), D(2026, 3, 4))
    assert P.last_week() == (D(2026, 2, 23), D(2026, 3, 1))


def test_months():
    assert P.month(2024, 2) == (D(2024, 2, 1), D(2024, 2, 29))
    assert P.this_month() == (D(2026, 3, 1), D(2026, 3, 4))
    assert P.same_days_last_month() == (D(2026, 2, 1), D(2026, 2, 4))
    assert Periods("2026-03-31").same_days_last_month() == (D(2026, 2, 1), D(2026, 2, 28))
    assert P.month_of(2) == P.month(2026, 2) and P.month_of(3) == P.month(2026, 3)
    assert P.month_of(4) == P.month(2025, 4)  # April has not started yet
    assert P.quarter(2025, 3) == (D(2025, 7, 1), D(2025, 9, 30))
    assert P.quarter_of(2) == P.quarter(2025, 2) and P.quarter_of(1) == P.quarter(2026, 1)
    assert P.ytd() == (D(2026, 1, 1), D(2026, 3, 4))


def test_prev():
    assert P.prev(P.month(2026, 1)) == P.month(2025, 12)
    assert P.prev(P.quarter(2026, 1)) == P.quarter(2025, 4)
    assert P.prev(P.year(2026)) == P.year(2025)
    assert P.prev(P.last_days(7)) == (D(2026, 2, 19), D(2026, 2, 25))
    assert P.prev(P.last_week()) == (D(2026, 2, 16), D(2026, 2, 22))


def test_tet():
    p = Periods("2026-06-01", read_lunar("data/train/train/calendar.csv"))
    assert p.tet(2026) == (D(2026, 2, 10), D(2026, 2, 26))  # 23 Chạp Ất Tỵ .. mùng 10 Giêng Bính Ngọ
    assert p.before_tet(2026) == (D(2026, 1, 24), D(2026, 2, 9))
