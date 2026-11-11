import json

import pandas as pd

from src.load import Table
from src.mapper import complete_manifest, needs_model, parse_patch


def _table():
    raw = pd.DataFrame({"Ngày": ["01/07/2025"], "Tiền": ["120000"], "Thợ": ["A.Đức"]})
    return Table("So", raw, raw, {"Ngày": "date", "Tiền": "number", "Thợ": "text"}, [("So.xlsx", "T7", 1)])


def test_parse_patch_keeps_only_what_exists():
    tables = {"So": _table()}
    reply = 'Đây là kết quả:\n' + json.dumps({"tables": {
        "So": {"role": "orders", "cols": {"Ngày": "date", "Tiền": "order_goods", "Thợ": "wizard", "Không có": "note"}},
        "Bảng lạ": {"role": "lines", "cols": {}},
    }})
    assert parse_patch(reply, tables) == {"So": {"cols": {"Ngày": "date", "Tiền": "order_goods"}, "role": "orders"}}
    assert parse_patch("không phải json", tables) == {}
    aloud = 'Draft {"tables": {"So": {"cols": {"Ngày": "ts"}}}} then { braces } and final {"tables": {"So": {"cols": {"Ngày": "date"}}}} ok'
    assert parse_patch(aloud, tables) == {"So": {"cols": {"Ngày": "date"}}}
    by_role = '{"tables": {"other": {"role": "other", "cols": {"So.Ngày": "date", "So.Tiền": "order_total", "Lạ.X": "note"}}}}'
    assert parse_patch(by_role, tables) == {"So": {"cols": {"Ngày": "date", "Tiền": "order_total"}}}
    assert parse_patch('{"tables": {"So": {"role": "nonsense", "cols": {"Ngày": null}}}}', tables) == {"So": {"cols": {"Ngày": None}}}


def test_complete_manifest():
    tables = {"So": _table()}
    manifest = {"tables": {"So": {"role": "other", "cols": {}, "unknown": ["Ngày", "Tiền", "Thợ"], "documented": True}}}
    assert needs_model(manifest)  # no usable sales table
    reply = '{"tables": {"So": {"role": "orders", "cols": {"Ngày": "date", "Tiền": "order_goods", "Thợ": "staff"}}}}'
    seen = []
    out, _ = complete_manifest(tables, {"text": "Sổ sửa xe"}, manifest, lambda msgs: seen.append(msgs) or reply)
    assert out["tables"]["So"] == {"role": "orders", "cols": {"Ngày": "date", "Tiền": "order_goods", "Thợ": "staff"},
                                   "unknown": [], "documented": True}
    assert "A.Đức" in seen[0][1]["content"] and "Sổ sửa xe" in seen[0][1]["content"]  # samples and README reach the model
    # a broken reply leaves the manifest alone
    before = json.dumps(out, ensure_ascii=False)
    complete_manifest(tables, {"text": ""}, out, lambda msgs: "lỗi")
    assert json.dumps(out, ensure_ascii=False) == before


def test_complete_manifest_settles_roles():
    tables = {"So": _table()}
    receipts = {"role": "orders", "cols": {"Thợ": "order_id"}, "unknown": ["Ngày", "Tiền"], "documented": False}
    reply = '{"tables": {"So": {"role": "orders", "cols": {"Ngày": "pay_ts", "Tiền": "pay_amount"}}}}'
    out, _ = complete_manifest(tables, {"text": ""}, {"tables": {"So": receipts}}, lambda msgs: reply)
    assert out["tables"]["So"]["role"] == "payments"  # a receipts table the rules took for orders
    book = {"role": "lines", "cols": {"Ngày": "date", "Tiền": "amount"}, "unknown": ["Thợ"], "documented": False}
    reply = '{"tables": {"So": {"role": "payments", "cols": {"Thợ": "pay_id"}}}}'
    out, _ = complete_manifest(tables, {"text": ""}, {"tables": {"So": book}}, lambda msgs: reply)
    assert out["tables"]["So"]["role"] == "lines"  # money of sales: one odd column does not move it
    repairs = {"role": "other", "cols": {}, "unknown": ["Ngày", "Tiền", "Thợ"], "documented": True}
    reply = '{"tables": {"So": {"role": "orders", "cols": {"Ngày": "date", "Tiền": "order_total", "Thợ": "prod_name"}}}}'
    out, _ = complete_manifest(tables, {"text": ""}, {"tables": {"So": repairs}}, lambda msgs: reply)
    assert out["tables"]["So"]["role"] == "orders"  # a name column alone does not make it a product list


def test_readme_order_rules():
    from src.schema import guess_table
    from src.shop import ROW_IS_ORDER, fold

    raw = pd.DataFrame({"Ngày": ["01/07/2025"], "STT": ["1"], "Hàng": ["Ốp"], "Thành tiền": ["120000"]})
    t = Table("Ban", raw, raw, {"Ngày": "date", "STT": "number", "Hàng": "text", "Thành tiền": "number"}, [("Ban.xlsx", None, 1)])
    readme = {"text": "- **Đơn**: các dòng cùng **STT** là một đơn; khách lặp lại.", "sections": []}
    lex = {"ngay": {"lines|date": 1}, "stt": {"lines|line_no": 3}, "hang": {"lines|prod_name": 1}, "thanhtien": {"lines|amount": 1}}
    assert guess_table(t, readme, lex)["cols"]["STT"] == "order_id"
    assert guess_table(t, {"text": "", "sections": []}, lex)["cols"]["STT"] == "line_no"
    assert ROW_IS_ORDER.search(fold("Sổ sửa xe: mỗi dòng một lượt xe vào sửa (một phiếu)"))
    assert not ROW_IS_ORDER.search(fold("Sổ bán hàng: mỗi dòng một mặt hàng khách mua, không ghi mã đơn"))


def test_needs_model():
    ok = {"role": "lines", "cols": {"a": "date", "b": "amount", "c": "status"}, "unknown": ["x"], "documented": True}
    assert not needs_model({"tables": {"t": ok}})  # described by the README: leftovers are just extra columns
    assert needs_model({"tables": {"t": {**ok, "documented": False}}})
    assert needs_model({"tables": {"t": {**ok, "cols": {"a": "date", "b": "amount"}}}})  # no status found
