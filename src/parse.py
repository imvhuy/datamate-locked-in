"""Parsers for the dirty cell formats in DataMate shops. Each returns None when the text is not that type.

Input is always a stripped string (the loader turns native Excel/SQLite values into strings first).
"""
import datetime as dt
import re
import unicodedata

_MULT = {"k": 1e3, "ngàn": 1e3, "nghìn": 1e3, "ngan": 1e3, "nghin": 1e3, "tr": 1e6, "triệu": 1e6, "trieu": 1e6}
_CUR = r"(?:đ|₫|vnd|vnđ|d|đồng|dong)"
_NUM = re.compile(
    rf"^([+-]?)\s*(\d[\d.,\s]*?)\s*(?:({'|'.join(_MULT)})\s*(\d*))?\s*{_CUR}?\s*(%?)$"
)
_QTY = re.compile(r"^([+-]?\d[\d.,]*)(\s*)([^\W\d_].*)$")  # "5 chai", "1 bao 25kg"


def fold(s):
    """Lowercase, strip Vietnamese diacritics: 'Đơn giá' -> 'don gia'. For matching, never for output."""
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")


def _body(b, has_unit):
    """Digits with separators -> float. With a k/tr unit a lone separator is a decimal mark ("4,544tr")."""
    if " " in b or "\xa0" in b:
        if not re.fullmatch(r"\d{1,3}([ \xa0]\d{3})+", b):  # "16 076"
            return None
        return float(re.sub(r"\s", "", b))
    if b.isdigit():
        return float(b)
    if not has_unit:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+|\d{1,3}(,\d{3})+", b):  # 560,000 / 270.000
            return float(re.sub(r"[.,]", "", b))
        if re.fullmatch(r"\d{1,3}(,\d{3})+\.\d+", b):  # 1,500.0000
            return float(b.replace(",", ""))
        if re.fullmatch(r"\d{1,3}(\.\d{3})+,\d+", b):  # 1.500,50
            return float(b.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d+[.,]\d+", b):  # 20,1 / 45000.0
        return float(b.replace(",", "."))
    return None


def parse_number(s):
    """'560,000' '270.000đ' '25k' '19tr990' '20,1tr' '1,500.0000' '16 076' '-45k' '12.5%' -> float."""
    m = _NUM.match(s.lower())
    if not m:
        return None
    sign, body, unit, tail, _ = m.groups()
    v = _body(body.strip(), bool(unit))
    if v is None:
        return None
    if tail:  # 19tr990 = 19.990 triệu, 3tr7 = 3.7 triệu
        if v != int(v):
            return None
        v += int(tail) / 10 ** len(tail)
    v = round(v * _MULT.get(unit, 1), 6)
    return -v if sign == "-" else v


def parse_qty(s):
    """'5 chai' -> (5.0, 'chai'), '1 bao 25kg' -> (1.0, 'bao 25kg'). Money suffixes are not units ('25k').

    Also matches '264 Lương Định Của'; the loader rejects columns with too many distinct "units".
    """
    m = _QTY.match(s)
    if not m:
        return None
    unit = m[3].strip()
    if re.fullmatch(rf"(?:{'|'.join(_MULT)})?\s*{_CUR}?", unit.lower()):
        return None
    if not m[2] and re.search(r"\d", unit):  # "8x8x18" is a size, not 8 of something
        return None
    v = parse_number(m[1])
    return None if v is None else (v, unit)


_T = r"(?:[ t]+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?"
_YMD = re.compile(rf"^(\d{{4}})[-/.](\d{{1,2}})[-/.](\d{{1,2}}){_T}$")
_DMY = re.compile(rf"^(\d{{1,2}})[-/.](\d{{1,2}})[-/.](\d{{4}}){_T}$")
_THG = re.compile(r"^(?:ngày\s+)?(\d{1,2})\s*(?:thg|tháng|thang)\.?\s*(\d{1,2})(?:\s*(?:,|năm|nam)?\s*(\d{4}))?$")
_DM = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_SERIAL = re.compile(r"^(\d{5})(\.\d+)?$")
_EPOCH = dt.datetime(1899, 12, 30)


def is_serial(s):
    """Excel day number between 2000 and 2050, e.g. '45717'."""
    m = _SERIAL.match(s)
    return bool(m) and 36526 <= int(m[1]) <= 54789


def parse_date(s, year=None, serial=True):
    """-> datetime (time 00:00 when the text has none) or None.

    Handles '01/07/2025', '2025-07-01 06:00:25', '1 thg 7 2025', Excel serials ('45717'),
    and year-less '1/7' or '1 thg 7' when `year` (from the sheet name) is given.
    """
    s = s.lower()
    try:
        if m := _YMD.match(s):
            y, mo, d, h, mi, se = m.groups()
        elif m := _DMY.match(s):
            d, mo, y, h, mi, se = m.groups()
        elif m := _THG.match(s):
            d, mo, y = m.groups()
            h = mi = se = None
        elif m := _DM.match(s):
            d, mo = m.groups()
            y = h = mi = se = None
        elif serial and is_serial(s):
            return _EPOCH + dt.timedelta(days=float(s))
        else:
            return None
        y = y or year
        if not y:
            return None
        return dt.datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(se or 0))
    except ValueError:  # 31/02, month 13...
        return None
