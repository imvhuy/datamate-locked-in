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


def test_vote():
    from src.agent import vote

    n5, n6, ab = {"type": "number", "answer": 5}, {"type": "number", "answer": 6}, {"type": "abstain"}
    assert vote([n5, n6, n5], 3) == 0  # two of three agree
    assert vote([n5, n6, ab], 3) is None  # no majority: abstain
    assert vote([ab, ab, ab], 3) is None
    assert vote([n5], 1) == 0
    un = {"type": "unanswerable", "reason": "NoData('the shop keeps no lines')"}
    assert vote([un, un, n5], 3) is None  # unanswerable needs every solve
    assert vote([un, un, un], 3) == 0
    out = {"type": "unanswerable", "reason": "the period lies wholly outside the data"}
    assert vote([out, out, n5], 3) == 0


def test_settle_stops_once_decided():
    from src.agent import settle

    n5, n6, ab = {"type": "number", "answer": 5}, {"type": "number", "answer": 6}, {"type": "abstain"}
    un = {"type": "unanswerable", "reason": "NoData('x')"}

    def solver(answers):
        calls = []

        def one(k):
            calls.append(k)
            return (answers[k],)

        return one, calls

    one, calls = solver([n5, n5, n6])
    assert settle(one, 3)[1] == 0 and sorted(calls) == [0, 1]  # two agree: the third is never run
    one, calls = solver([n5, n6, n5])
    assert settle(one, 3)[1] == 0 and sorted(calls) == [0, 1, 2]  # split: the third decides
    one, calls = solver([ab, ab, n5])
    assert settle(one, 3)[1] is None and sorted(calls) == [0, 1]  # no majority possible any more
    one, calls = solver([un, un, un])
    assert settle(one, 3)[1] == 0 and sorted(calls) == [0, 1, 2]  # unanswerable needs every solve


def test_from_thinking_takes_last_program_and_type():
    from src.agent import from_thinking

    th = 'Draft:\n```python\nx = 1\n```\nThe type should be "percent".\n```python\nanswer = m.pct_change(1, 2)\n```\n'
    assert from_thinking(th) == ("percent", "answer = m.pct_change(1, 2)")
    assert from_thinking("no code, type number") == (None, None)
    assert from_thinking(None) == (None, None)


def test_check_flags_comparing_a_column_with_a_period():
    from types import SimpleNamespace

    from src.agent import check

    shop = SimpleNamespace(products=None, find_prod=lambda x: None)
    probs = check('answer = len(T["X"][T["X"]["Ngày"] <= P.today()])', "tới hôm nay mấy phiếu?", shop)
    assert any("(start, end)" in p for p in probs)
    assert not check('answer = len(m.rows(T["X"], "Ngày", P.today()))', "tới hôm nay mấy phiếu?", shop)
