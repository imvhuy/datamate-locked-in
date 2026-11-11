from types import SimpleNamespace

from src.agent import as_program, check, parse_reply
from src.metrics import NoData, is_no_data
from src.shop import key


def test_as_program():
    assert as_program("m.pct_change(1, 2)") == "answer = m.pct_change(1, 2)"
    assert as_program("type: number\nanswer = 5") == "answer = 5"
    assert as_program("x = 1\nx + 1") == "x = 1\nanswer = x + 1"
    assert as_program("answer = <expression>") == "answer = <expression>"  # left for exec to reject


def test_is_no_data():
    assert is_no_data(NoData("x"))
    assert is_no_data(KeyError("cust_group"))  # the shop has no such column
    assert not is_no_data(KeyError("answer"))  # a program bug, not missing data
    assert not is_no_data(KeyError(None))


def test_parse_reply():
    ok = "type: number\n```python\nanswer = m.revenue(P.today())\n```"
    assert parse_reply(ok) == ("number", "answer = m.revenue(P.today())")
    inside = "```python\ntype: percent\nanswer = m.cancel_rate(P.this_month())\n```"
    assert parse_reply(inside) == ("percent", "answer = m.cancel_rate(P.this_month())")
    copied = "type: number\n```python\nQ: tháng 1 bán bao nhiêu\nanswer = m.revenue(P.month(2025, 1))\n```"
    assert parse_reply(copied) == ("number", "answer = m.revenue(P.month(2025, 1))")
    assert parse_reply("type: number\nanswer = m.n_orders(P.today())") == ("number", "answer = m.n_orders(P.today())")
    assert parse_reply("không biết") == (None, None)


class FakeShop(SimpleNamespace):
    def find_prod(self, text):
        return {key(n): n for n in self.names}.get(key(text))


SHOP = FakeShop(names=["Trà sữa ô long (M)", "Costar Omega 3 1000mg lọ 100 viên"], products=True)


def test_check_catches_copied_values():
    q = "trong thang 2 nam 2025 cua hang ban duoc may ly tra sua o long (m)"
    assert check("answer = m.qty(P.month(2025, 2), prod='Trà sữa ô long (M)')", q, SHOP) == []
    assert check("answer = m.qty(P.month(2025, 4), prod='Trà sữa ô long (M)')", q, SHOP)  # month from an example
    assert check("answer = m.qty(P.month(2024, 2), prod='Trà sữa ô long (M)')", q, SHOP)  # year from an example
    assert check("answer = m.qty(P.month(2025, 2), prod='Rau muống xào tỏi')", q, SHOP)  # another shop's product
    q2 = "Số lọ Costar Omega 3 1000mg lọ 100 viên bán được trong tháng 3/2026"
    assert check("answer = m.qty(P.month(2026, 3), prod='Trà sữa ô long (M)')", q2, SHOP)  # not the product asked
    assert check("answer = m.price_on('Product Name', 'YYYY-MM-DD')", q2, SHOP)  # placeholder
    assert check("answer = m.revenue(P.month(2025, 12), group='ống nước')", "Tỉ trọng nhóm gỏi tháng 12/2025", SHOP)


def test_check_allows_derived_periods():
    q = "So cùng kỳ năm trước, doanh thu tháng 5/2026 tăng/giảm mấy phần trăm?"
    assert check("answer = m.pct_change(m.revenue(P.month(2026, 5)), m.revenue(P.month(2025, 5)))", q, SHOP) == []
    q = "Từ tháng 8/2025 tới tháng 10/2025, tháng nào bán được nhiều tiền nhất?"
    assert check("answer = m.best_month([P.month(2025, 8), P.month(2025, 9), P.month(2025, 10)])", q, SHOP) == []


def test_split_thinking():
    from src.agent import split_thinking

    assert split_thinking({"content": "ans", "reasoning": "hmm"}, "stop") == ("ans", "hmm")
    assert split_thinking({"content": "ans", "reasoning_content": "hmm"}, "stop") == ("ans", "hmm")
    assert split_thinking({"content": "I think...\n</think>\n\ntype: number"}, "stop") == ("type: number", "I think...")
    assert split_thinking({"content": "<think>a</think>b"}, "stop") == ("b", "a")
    assert split_thinking({"content": "still thinking and then cut"}, "length") == ("", "still thinking and then cut")
    assert split_thinking({"content": "type: number\n```python\nanswer = 1\n```"}, "stop")[1] is None


def test_shaky_when_the_model_mapped_the_shop():
    from src.agent import shaky

    known = {"tables": {"t": {"role": "lines", "cols": {"a": "date"}, "unknown": [], "documented": False}}}
    assert not shaky(SimpleNamespace(manifest=known, lines=True, orders=None))
    assert shaky(SimpleNamespace(manifest={**known, "by_model": True}, lines=True, orders=None))
