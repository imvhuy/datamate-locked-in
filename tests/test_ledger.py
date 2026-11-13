"""Side books: entry rows filled in (text and dates only), order references read in any spelling."""
import pandas as pd

from src.ledger import entries, norm_id


def _book():
    d = pd.to_datetime
    return pd.DataFrame({
        "Ngày": [d("2025-07-02"), d("2025-07-03"), pd.NaT, pd.NaT, d("2025-07-06"), pd.NaT],
        "Khách": ["An", "Bình", None, None, "Chi", None],
        "Nợ": [270.0, 240.0, None, None, 900.0, None],
        "Đã trả": [270.0, 80.0, 80.0, 80.0, 300.0, 300.0],
        "Ngày trả": [d("2025-07-30"), d("2025-07-17"), d("2025-07-25"), d("2025-08-02"), d("2025-07-12"), d("2025-07-21")],
        "Ghi chú": ["STT 26", "STT 34", "STT 34", "STT 34", "STT 70", "STT 70"],
    })  # fmt: skip


def test_entries_fill_text_and_dates_never_amounts():
    out, n = entries(_book())
    assert n == 3
    assert out["Khách"].tolist() == ["An", "Bình", "Bình", "Bình", "Chi", "Chi"]
    assert out["Ngày"].isna().sum() == 0
    assert out["Nợ"].sum() == 270 + 240 + 900  # amounts are never copied
    assert out["Ngày trả"].tolist() == _book()["Ngày trả"].tolist()  # cells already written stay
    assert out["_entry"].tolist() == [1, 2, 2, 2, 3, 3]


def test_a_list_with_blank_dates_is_not_a_book():
    df = pd.DataFrame({"Tên": [f"K{i}" for i in range(8)], "SĐT": ["09"] * 8, "Ngày đăng ký": pd.to_datetime(
        ["2025-01-01", None, None, "2025-02-01", None, "2025-03-01", None, None])})  # fmt: skip
    assert entries(df)[1] == 0


def test_norm_id():
    assert norm_id(26.0) == norm_id("26") == norm_id("STT 26") == "26"
    assert norm_id("ORD000001") == "ORD000001"
    assert norm_id(None) is None
