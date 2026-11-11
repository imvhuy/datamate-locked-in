import pandas as pd

from src.load import cell, find_header, infer, is_footer


def test_cell():
    assert cell(45717.0) == "45717" and cell(0.5) == "0.5" and cell(None) == "" and cell("  a   b ") == "a b"


def test_find_header():
    data = ["01/02/2025", "ORD1", "Cà phê", "25k", "x"]
    assert find_header([["A", "B", "C", "D", "E"], data]) == 0
    # report title, blank line, then header
    assert find_header([["SỔ CHI TIẾT", "", "", "", ""], [""] * 5, ["A", "B", "C", "D", "E"], data]) == 2
    # merged group header above the real one
    assert find_header([["Hàng", "", "", "Tiền", ""], ["A", "B", "C", "D", "E"], data]) == 1
    assert find_header([[""], ["Ngày", "Xe", "Tiền"], ["01/07/2025", "wave", "120000"]]) == 1


def test_is_footer():
    w = 8
    assert is_footer(["", "3,411,000", "", "", "Tổng ngày 01/03", "", "", ""], w)
    assert is_footer(["Cộng", "", "", "280.183.667đ", "", "", "", ""], w)
    assert is_footer(["Ghi chú: tiền tính bằng đồng"] + [""] * 7, w)
    assert is_footer(["Tong cong", "5000"] + [""] * 6, w)
    # sparse continuation rows of a debt ledger are data
    assert not is_footer(["", "", "", "", "80k", "45863", "STT 34", ""], w)
    # a full data row that happens to start with "Tổng"
    assert not is_footer(["Tổng hợp vitamin", "1", "hộp", "50k", "50k", "xong", "ORD1", "lẻ"], w)


def _kind(values, name="x", year=None):
    s = pd.Series(values)
    return infer(s, pd.Series([year] * len(s), dtype="object"), name)[0]


def test_infer():
    assert _kind(["45717", "01/03/2025", "1 thg 3 2025"]) == "date"
    assert _kind(["45717", "45718"], name="Ngày bán") == "date"
    assert _kind(["45717", "45718"], name="Giá") == "number"  # serial-looking prices stay numbers
    assert _kind(["1/1", "2/1"], name="Ngày lập", year=2025) == "date"
    assert _kind(["2025-02-01 06:00:25", "2025-02-01 07:00:00"]) == "datetime"
    assert _kind(["25k", "270.000đ", "19tr990", ""]) == "number"
    assert _kind(["1 hộp", "5 chai", "2", "1 bao 25kg"]) == "number"
    assert _kind(["0909377857", "0359938682"]) == "text"  # phone
    assert _kind(["358841973775088", "353277390076765"]) == "text"  # IMEI
    assert _kind([f"{i} Đường số {i}" for i in range(1, 60)]) == "text"  # addresses
    assert _kind(["608 Mai Chí Thọ", "997 Tỉnh Lộ 10", "71 Tên Lửa"]) == "text"  # few streets, still addresses
    assert _kind(["ORD000001", "ORD000002"]) == "text"
    assert _kind(["", ""]) == "empty"


def test_status():
    from src.shop import status_of

    for text, want in [("Đang vận chuyển", "shipping"), ("Đã hủy đơn", "cancel"), ("khách huỷ", "cancel"),
                       ("Không giao được", "cancel"), ("Đã trả hàng", "return"), ("Hoàn trả", "return"),
                       ("Đã hoàn thành", "done"), ("đã lấy", "done"), ("Đang giao dịch", "shipping"), ("ok", "done")]:
        assert status_of(text) == want, text
