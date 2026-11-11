"""Run: python -m pytest tests -q"""
import datetime as dt

import pytest

from src.parse import is_serial, parse_date, parse_number, parse_qty

NUMBERS = {
    "560,000": 560000, "270.000đ": 270000, "25k": 25000, "19tr990": 19990000, "20,1tr": 20100000,
    "1tr440": 1440000, "16,56tr": 16560000, "9tr960": 9960000, "1,500.0000": 1500, "92,286.0000": 92286,
    "-30.500đ": -30500, "-45k": -45000, "2131200.0": 2131200, "18400k": 18400000, "4,544tr": 4544000,
    "294,7703tr": 294770300, "294.770.300đ": 294770300, "16 076": 16076, "3tr7": 3700000, "45000.0": 45000,
    "0": 0, "1.0": 1, "12.5%": 12.5, "131,789,600": 131789600, "0.5": 0.5, "1.300đ": 1300, "6,5": 6.5,
}
NOT_NUMBERS = ["", "ORD000001", "1 hộp", "Số 1", "2025-02-01", "1/1", "IP16 128 đen", "đ", "k", "12 5", "1.2.3"]


@pytest.mark.parametrize("s,want", NUMBERS.items())
def test_number(s, want):
    assert parse_number(s) == pytest.approx(want)


@pytest.mark.parametrize("s", NOT_NUMBERS)
def test_not_number(s):
    assert parse_number(s) is None


def test_qty():
    assert parse_qty("5 chai") == (5.0, "chai")
    assert parse_qty("1 kg") == (1.0, "kg")
    assert parse_qty("15 vỉ") == (15.0, "vỉ")
    assert parse_qty("3 m") == (3.0, "m")
    assert parse_qty("1 bao 25kg") == (1.0, "bao 25kg")
    assert parse_qty("2 hộp 30") == (2.0, "hộp 30")
    for s in ["25k", "270.000đ", "8x8x18", "2025-02-01", "Cáp C", "19tr990", "5"]:
        assert parse_qty(s) is None, s


DATES = {
    "01/07/2025": (2025, 7, 1), "1 thg 7 2025": (2025, 7, 1), "2025-07-01": (2025, 7, 1),
    "45717": (2025, 3, 1), "45689": (2025, 2, 1), "1/7/2025": (2025, 7, 1), "2024-07-14 00:00:00": (2024, 7, 14),
    "1 tháng 7 năm 2025": (2025, 7, 1), "01-07-2025": (2025, 7, 1),
}


@pytest.mark.parametrize("s,ymd", DATES.items())
def test_date(s, ymd):
    assert parse_date(s) == dt.datetime(*ymd)


def test_date_misc():
    assert parse_date("2025-02-01 06:00:25") == dt.datetime(2025, 2, 1, 6, 0, 25)
    assert parse_date("2025-03-01 06:02") == dt.datetime(2025, 3, 1, 6, 2)
    assert parse_date("1/1", year=2025) == dt.datetime(2025, 1, 1)
    assert parse_date("1/1") is None  # no year known
    assert parse_date("45717", serial=False) is None
    for s in ["31/02/2025", "ORD000001", "45000.5x", "560,000", "", "13/13/2025", "12345678"]:
        assert parse_date(s) is None, s
    assert is_serial("45717") and not is_serial("4571") and not is_serial("99999")
