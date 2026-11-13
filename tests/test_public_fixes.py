"""Shop shapes first seen on public test: debt written in notes, abbreviated product names, call names for staff."""
import pandas as pd

from src.schema import fit_fields
from src.shop import _book_names, _group_column, note_debt


def test_note_debt():
    assert note_debt("khách đưa trước 87k, còn thiếu 73 nghìn", 160000) == 73000
    assert note_debt("trả 259k, nợ 449.500đ", 708500) == 449500
    assert note_debt("đưa trước 236k, còn lại ghi sổ", 500000) == 264000
    assert note_debt("nợ cả đơn", 777) == 777
    assert note_debt("nợ 69 nghìn, khách ck đủ rồi", 69000) == 0
    assert note_debt("ít đá", 5) == 0 and note_debt(None, 5) == 0


def test_expense_date_is_its_own_field():
    cols = fit_fields("expenses", {"Ngày mua": "date", "Nhóm": "cust_group", "Tiền chi": "exp_amount"})
    assert cols == {"Ngày mua": "exp_date", "Nhóm": "exp_cat", "Tiền chi": "exp_amount"}


def test_book_names_and_group_from_abbreviation_sheet():
    lines = pd.DataFrame({"prod_name": ["CL 9D", "Sạc 20W", "CL 9D"], "prod": ["cl 9d", "sac 20w", "cl 9d"]})
    cat = pd.DataFrame({"Ghi tắt": ["CL 9D", "Sạc 20W"], "prod_name": ["Kính cường lực 9D", "Củ sạc nhanh 20W"],
                        "Loại": ["phụ kiện", "phụ kiện"]})  # fmt: skip
    out = _book_names(lines, cat)
    assert list(out["prod_name"]) == ["Kính cường lực 9D", "Củ sạc nhanh 20W", "Kính cường lực 9D"]
    assert list(out["book_name"]) == ["CL 9D", "Sạc 20W", "CL 9D"]
    assert "group" in _group_column(cat)


def test_split_toppings_written_with_the_dish():
    from src.shop import split_toppings

    lines = pd.DataFrame({"order_id": [1, 2, 3], "prod_name": ["Matcha latte (M) + 2 Thạch cà phê (14.000đ)", "Topping Thạch cà phê", "Trà chanh (M)"],
                          "qty": [2.0, 1.0, 1.0], "price": [49000.0, 7000.0, 26000.0], "amount": [112000.0, 7000.0, 26000.0], "unit": ["ly"] * 3})  # fmt: skip
    out = split_toppings(lines)
    assert list(out["prod_name"]) == ["Matcha latte (M)", "Topping Thạch cà phê", "Topping Thạch cà phê", "Trà chanh (M)"]
    assert list(out["qty"]) == [2, 2, 1, 1] and list(out["amount"]) == [98000, 14000, 7000, 26000]
    assert out["amount"].sum() == lines["amount"].sum() and out["prod"][1] == "topping thach ca phe"


def test_customer_identity_rules():
    from src.shop import cust_key, is_charge, plate_of

    assert cust_key("Trịnh Hằng Nam - 0977 824 094") == cust_key("Trịnh Hằng Nam (0977824094)")
    assert cust_key("Lưu Hà An") != cust_key("Lưu Hà Anh")
    assert plate_of("Vision 110 51e5-585.28") == plate_of("vision 51E5 58528") == "51E5-585.28"
    assert plate_of("khách lẻ") is None
    assert is_charge("Voucher") and is_charge("Phí ship") and is_charge("Dùng 200 điểm")
    assert not is_charge("Phí phục vụ") and not is_charge("Sữa Enfamil số 1")


def _metrics():
    from types import SimpleNamespace

    from src.metrics import Metrics

    d = pd.to_datetime
    o = pd.DataFrame({
        "order_id": [1, 2, 3, 4, 5, 6, 7],
        "o_date": d(["2026-03-02", "2026-03-02", "2026-03-03", "2026-03-09", "2026-03-09", "2026-03-10", "2026-03-10"]),
        "o_ts": d(["2026-03-02 08:10", "2026-03-02 09:05", "2026-03-03 08:59", "2026-03-09 08:00", "2026-03-09 15:00", "2026-03-10 09:30", "2026-03-10 10:00"]),
        "o_status": ["done", "done", "done", "done", "done", "cancel", "done"],
        "cust": ["a", "a", "a", "b", "b", "b", None], "cust_name": ["An", "An", "An", "Bình", "Bình", "Bình", None],
        "staff": ["NV2", "NV1", "NV1", "NV2", "NV2", "NV1", "NV2"],
    })  # fmt: skip
    lines = o.assign(st=o["o_status"], prod=["x", "y", "x", "x", "z", "x", "y"], amount=[10, 20, 30, 100, 100, 5, 1], is_item=True)
    shop = SimpleNamespace(lines=lines, orders=o.assign(st=o["o_status"]), readme={}, notes=[], products=None, by_role={},
                           name_of=lambda x: x.upper())  # fmt: skip
    return Metrics(shop, "2026-03-31"), (pd.Timestamp("2026-03-01").date(), pd.Timestamp("2026-03-31").date())


def test_public_ranking_rules():
    m, p = _metrics()
    assert m.loyal_customers(p) == ["An"]  # 2 days against Bình's 1 (the cancelled order does not count)
    assert m.big_spenders(p) == ["An"]  # Bình has only 2 counted orders
    assert m.fickle_customers(p) == ["Bình"]
    assert m.popular_products(p) == ["X"]
    assert m.hardest_workers(p) == ["NV2"]  # 3 days against NV1's 2
    assert m.busiest_day(p) == "2026-03-02"  # 2 orders, tied with the 9th: the earlier day
    assert m.peak_hour(p) == 8
    assert m.weekday_avg(p, 0) == 46  # (30 + 200) / 5 Mondays in March 2026, two of them with sales
    assert m.weekday_avg(p, 0, of="orders") == 0.8




def test_periods_the_question_names():
    import datetime as dt

    from src.asked import period_problems
    from src.period import Periods

    P = Periods("2026-06-30")
    month = P.month(2026, 5)
    assert period_problems("5/26 phi ship cua tiem la bao nhieu?", P, [P.day("2026-05-26")])
    assert period_problems("5/26 phi ship cua tiem la bao nhieu?", P, [month]) == []
    assert period_problems("Doanh thu T1/2026 quán được bao nhiêu?", P, [P.quarter(2026, 1)])
    assert period_problems("quy 4/2025 tiem co bao nhieu don", P, [P.quarter(2025, 4)]) == []
    assert period_problems("Tu 15/02/2026 den het 21/02/2026 co bnhiu don?", P, [P.between("2026-02-15", "2026-02-21")]) == []
    assert period_problems("Hqua tiệm bán mấy gói vậy?", P, [P.today()])
    assert period_problems("tu dau nam den het hnay ban duoc bnhiu tien", P, [P.ytd()]) == []
    assert period_problems("Thang nay giao may dot?", P, [P.month(2026, 6)]) == []  # nothing after today exists
    assert period_problems("Tính luôn hnay thì 7 ngày gần nhất có bnhiu đơn?", P, [P.last_days(7)]) == []
    assert period_problems("T7/2025 doanh thu?", P, [(dt.date(2025, 7, 1), "x")]) == []  # dates built by hand


def test_result_checks():
    from types import SimpleNamespace

    from src.period import Periods
    from src.verify import result_problems

    m = SimpleNamespace(P=Periods("2026-06-30"), shop=None)
    assert result_problems({"type": "number", "answer": 0}, "Tháng ni bnhiu phiếu sửa có vá ruột xe?", m)
    assert result_problems({"type": "number", "answer": 0}, "7 ngày gần nhất có mấy mặt hàng ế?", m) == []
    assert result_problems({"type": "percent", "answer": -100}, "Th này đơn có thu máy cũ chiếm mấy % đơn hàng?", m)
    assert result_problems({"type": "percent", "answer": -5.94}, "dthu tăng hay giảm mí % so vs thang trc", m) == []
    assert result_problems({"type": "percent", "answer": 140}, "nhóm trà chiếm bao nhiêu % doanh thu tháng 2/2026", m)
    assert result_problems({"type": "date", "answer": "2026-05-03"}, "Ngày dthu cao nhất T7/2026?", m)
    assert result_problems({"type": "date", "answer": "2026-07-05"}, "Ngày dthu cao nhất T7/2026?", m) == []
    assert result_problems({"type": "list", "answer": ["a", "b"]}, "top 3 hàng bán chạy tháng 5/2026", m)


def test_reply_forms_from_public_logs():
    from src.agent import as_day, from_thinking, parse_reply

    typ, code = parse_reply('type: unanswerable\n```python\nanswer = no_data("không ghi giới tính")\n```')
    assert typ == "number" and "no_data(" in code
    assert parse_reply("type: unanswerable")[1].startswith("answer = no_data(")
    assert from_thinking('so the data lacks it:\n```python\nanswer = no_data("no address")\n```')[0] == "number"
    assert as_day("tháng 7/25") == "2025-07" and as_day("Tháng 5/2025") == "2025-05" and as_day("2026-02") == "2026-02"
    assert as_day("(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))") == "2026-03"
    assert as_day("Zalo") is None and as_day("51E5-585.28") is None and as_day(["2026-03-01", "2026-05-31"]) is None


def test_one_name_in_a_list_answers_a_who_question():
    from types import SimpleNamespace

    from src.agent import run_program
    from src.period import Periods

    m = SimpleNamespace(P=Periods("2026-06-30"), shop=SimpleNamespace(views={"x": 1}, tables={}))
    assert run_program("string", "answer = ['Hà Loan Đạt']", m)[0] == {"type": "string", "answer": "Hà Loan Đạt"}
    assert run_program("date", "answer = 'tháng 7/25'", m)[0] == {"type": "date", "answer": "2025-07"}


def test_until_today_is_not_today():
    from src.asked import period_problems
    from src.period import Periods

    P = Periods("2025-09-30")
    assert period_problems("Hết ngày hôm nay, tổng số tiền góp khách chưa trả là bao nhiêu?", P, [P.today()])
    assert period_problems("Hôm nay là ngày bán được bao nhiêu tiền?", P, [P.today()]) == []
    assert period_problems("Cả ngày hôm nay bán được bao nhiêu tiền?", P, [P.today()]) == []


def test_lenient_api():
    import pandas as pd
    import pytest

    from src.agent import Tables
    from src.metrics import as_ts
    from src.period import Periods

    T = Tables({"QuanLy.KhachHang": 1, "SoNo.Trả góp": 2})
    assert T["KhachHang"] == 1 and T["tra gop"] == 2 and T["SoNo.Trả góp"] == 2
    with pytest.raises(KeyError, match="the tables are"):
        T["Kho"]
    P = Periods("2026-08-21")
    assert P.day(2026, 8, 21) == P.today() and P.between(P.today(), P.days_from(P.today(), 10))[1].day == 30
    assert as_ts(P.month(2025, 12)) == pd.Timestamp("2025-12-31") and as_ts("2025-12-05") == pd.Timestamp("2025-12-05")
