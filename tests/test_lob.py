"""Line-of-business rules of the public-test rulebook (§6), on made-up rows."""
from types import SimpleNamespace

import pandas as pd

from src.metrics import (
    _month_day,
    attr_is,
    brand_of,
    does_job,
    model_line,
    vehicle_lines,
)
from src.rules import Rulebook
from src.verify import helper_calls, lines_of_business, rule_problems


def test_repair_jobs_read_by_the_rulebook():
    cases = [("vá ruột, thay nhớt", "vá ruột", True), ("thay ruột", "vá", False), ("ruột, thay bóng đèn", "thay ruột", True),
             ("vá ruột", "thay ruột", False), ("nhớt láp", "thay nhớt", False), ("castrol", "nhớt", True),
             ("thay nhớt láp", "dầu láp", True), ("thay bóng đèn", "sửa điện", False), ("làm điện", "sửa điện", True),
             ("bình GS", "sửa điện", False), ("bdtq + vs nồi", "bảo dưỡng", True), ("bh vá", "vá", False)]  # fmt: skip
    assert [(c, j) for c, j, want in cases if does_job(c, j) != want] == []


def test_vehicle_lines_merge_spellings():
    cells = ["AB 59X1-123.45", "Air Blade 59X1-223.45", "airblade 60B2-111.22", "Ex 61A1-999.00", "Exciter 61A1-998.00",
             "Wave α 50K2-994.07", "Wave Alpha 50K2-994.08", "Alpha 50K2-994.09", "shmode 61K5-628.79", "SH Mode 61K5-628.70"]  # fmt: skip
    shop = SimpleNamespace(orders=pd.DataFrame({"cust_name": cells}), lines=None)
    names = set(vehicle_lines(shop).values())
    assert names == {"Air Blade", "Exciter", "Wave Alpha", "SH Mode"}


def test_phone_names_and_catalogue_attributes():
    assert model_line("iPhone 16 128GB Đen") == "iPhone 16" and model_line("Nokia 105 4G") == "Nokia 105 4G"
    assert brand_of("iPhone 13 Pro") == "Apple" and brand_of("Samsung Galaxy A56 5G 128GB") == "Samsung"
    assert attr_is("Size XL", "XL") and not attr_is("Size XXL", "XL") and attr_is("Số 2", 2) and not attr_is(None, "M")


def test_instalment_due_days():
    assert _month_day(pd.Timestamp("2025-07-03"), 1, 3) == pd.Timestamp("2025-08-03")
    assert _month_day(pd.Timestamp("2026-01-20"), 1, 31) == pd.Timestamp("2026-02-28")  # a shorter month: its last day


def test_rule_mismatch_only_on_the_shops_own_trade():
    rb = Rulebook("## 6. Theo ngành\n### Sửa xe\n| Luật | Mặc định |\n|---|---|\n"
                  "| Xe sửa lại | trung bình một phiếu sửa lại chia cho số phiếu có thu tiền |\n")  # fmt: skip
    repair = SimpleNamespace(readme={"text": "# Dữ liệu x\n\nSổ sửa xe chủ tiệm tự ghi.\n\n## File\n"})
    grocery = SimpleNamespace(readme={"text": "# Dữ liệu y\n\nFile Excel bán hàng. Đơn qua điện thoại.\n\n## File\n"})
    q, code = "Trung bình một phiếu sửa lại tháng 3/2026 bao nhiêu tiền?", "answer = m.avg_order(P.month(2026, 3))"
    assert lines_of_business(rb, repair) and not lines_of_business(rb, grocery)
    assert rule_problems(q, code, rb, repair) and not rule_problems(q, code, rb, grocery)
    assert helper_calls("df = m.orders(P.month(2026, 3))\nanswer = len(df[df['x'] > 0])") is None  # its own pandas: no doubt
