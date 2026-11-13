from types import SimpleNamespace

import pytest

from src.agent import Limited, api_doc, uses
from src.rules import Rulebook, parse

BOOK = """# Rulebook
Mọi đáp án tính theo luật dưới đây.

## 3. Xếp hạng
| Luật | Mặc định |
|---|---|
| Nhân viên bán giỏi nhất | theo **doanh thu** của nhân viên |
| Nhân viên, thợ chăm chỉ nhất | người có **nhiều ngày làm việc nhất** trong kỳ |

### Sửa xe
| Luật | Mặc định |
|---|---|
| Thợ làm nhiều nhất | theo **số lượt xe** |
"""


def test_parse_rows_and_sections():
    rules = parse(BOOK)
    rows = [r for r in rules if r["key"]]
    assert [r["key"] for r in rows] == ["Nhân viên bán giỏi nhất", "Nhân viên, thợ chăm chỉ nhất", "Thợ làm nhiều nhất"]
    assert rows[1]["text"] == "người có nhiều ngày làm việc nhất trong kỳ"  # bold marks dropped
    assert rows[2]["section"] == "3. Xếp hạng / Sửa xe"
    assert any(r["text"].startswith("Mọi đáp án") for r in rules)  # prose outside tables is kept too


def test_top_finds_the_rule_named_in_the_question():
    top = Rulebook(BOOK).top("tháng 3 ai là thợ chăm chỉ nhất", k=1)
    assert top[0]["key"] == "Nhân viên, thợ chăm chỉ nhất"


def test_hidden_methods():
    assert uses("answer = m.top_staff(P.this_month())", {"top_staff"})
    assert not uses("answer = m.revenue(P.this_month())", {"top_staff"})
    doc = api_doc(frozenset({"cancel_rate"}))
    assert "cancel_rate" not in doc and "[rule: Doanh thu một đơn]" in doc
    m = Limited(SimpleNamespace(revenue=lambda p: 5, top_staff=lambda p: "An"), {"top_staff"})
    assert m.revenue(None) == 5
    with pytest.raises(AttributeError):
        m.top_staff(None)
