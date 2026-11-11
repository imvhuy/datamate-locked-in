"""Let the model finish a manifest the rules could not: README prose + column samples -> role and field per column.

The deterministic guess (src/schema.py) handles shops whose README describes each column or whose column names
are in the lexicon. Anything else (a silent README, a new line of business) goes through `complete_manifest`,
which asks the model once per shop and accepts only answers that name real tables, real columns and known fields.

    manifest = complete_manifest(tables, readme, guess_manifest(tables, readme), ask)   # ask(messages) -> text
"""
import json
import re

from src.schema import ROLE_RULES, role_of

# Canonical fields a column can be mapped to. The text is what the model reads, so it is in Vietnamese.
FIELDS = {
    "order_id": "mã đơn / số hoá đơn / số phiếu bán (nhiều dòng cùng mã là một đơn)",
    "line_no": "số thứ tự dòng trong đơn",
    "date": "ngày bán / ngày đặt đơn",
    "ts": "thời điểm tạo đơn (ngày giờ)",
    "status": "trạng thái đơn (hoàn thành, huỷ, trả hàng, đang giao)",
    "note": "ghi chú",
    "cust_id": "mã khách",
    "cust_name": "tên khách",
    "cust_group": "nhóm khách (sỉ, lẻ, thân thiết...)",
    "phone": "số điện thoại",
    "address": "địa chỉ",
    "staff": "nhân viên bán / thợ làm (mã hoặc tên ghi trên đơn)",
    "staff_name": "họ tên nhân viên trong danh sách nhân viên",
    "channel": "kênh bán (tại quầy, điện thoại, Zalo, sàn)",
    "payment": "hình thức thanh toán của đơn",
    "prod_id": "mã hàng",
    "prod_name": "tên hàng",
    "group": "nhóm hàng",
    "qty": "số lượng theo đơn vị của dòng",
    "unit": "đơn vị tính của dòng",
    "base_qty": "số lượng đã quy về đơn vị cơ bản",
    "base_unit": "đơn vị cơ bản của mặt hàng",
    "big_unit": "đơn vị lớn (thùng, hộp, tấn) trong bảng quy đổi",
    "factor": "hệ số quy đổi: số đơn vị cơ bản trong một đơn vị lớn",
    "price": "đơn giá bán trên dòng",
    "amount": "thành tiền của dòng, sau giảm giá dòng",
    "line_disc": "tiền giảm của dòng",
    "cost": "giá vốn một đơn vị bán, ghi trên dòng bán",
    "prod_cost": "giá vốn một đơn vị cơ bản trong danh mục hàng",
    "list_price": "giá bán hiện tại trong danh mục hàng",
    "order_goods": "tổng tiền hàng của đơn, trước giảm giá đơn",
    "order_disc": "giảm giá của cả đơn",
    "ship": "phí giao hàng",
    "tax": "thuế VAT",
    "order_total": "số khách phải trả cho cả đơn",
    "paid": "khách đã trả",
    "debt": "còn nợ",
    "pay_id": "mã phiếu thu",
    "pay_ts": "thời điểm thu tiền",
    "pay_amount": "số tiền của một lần thu",
    "po_id": "mã phiếu nhập",
    "po_line_no": "số thứ tự dòng trong phiếu nhập",
    "po_date": "ngày nhập",
    "po_status": "trạng thái phiếu nhập",
    "po_qty": "số lượng nhập theo đơn vị nhập",
    "po_unit": "đơn vị nhập",
    "po_price": "giá nhập một đơn vị nhập",
    "po_amount": "tiền nhập của dòng",
    "po_total": "tổng tiền của phiếu nhập",
    "supplier_id": "mã nhà cung cấp",
    "supplier": "tên nhà cung cấp",
    "stock_qty": "tồn kho cuối ngày cuối dữ liệu (đơn vị cơ bản)",
    "stock_after": "tồn sau mỗi lần nhập/bán trong thẻ kho",
    "led_ts": "thời điểm phát sinh trong thẻ kho",
    "led_type": "loại phát sinh trong thẻ kho",
    "led_qty": "số lượng phát sinh trong thẻ kho",
    "doc_type": "loại chứng từ gốc của dòng thẻ kho",
    "doc_id": "số chứng từ gốc của dòng thẻ kho",
    "price_date": "ngày bắt đầu áp dụng giá",
    "new_price": "giá bán áp dụng từ ngày đó",
    "old_price": "giá trước lần đổi",
    "lot": "số lô",
    "expiry": "hạn sử dụng",
    "lot_in_qty": "số lượng nhập của lô (đơn vị cơ bản)",
    "lot_qty": "số lượng bán lấy từ lô (đơn vị cơ bản)",
    "lot_ret_qty": "số lượng khách trả lại về lô",
    "ingredient": "hoạt chất",
    "rx": "thuốc kê đơn (đúng/sai)",
    "deliv_date": "ngày giao hàng",
    "deliv_no": "đợt giao thứ mấy",
    "due_date": "hạn thanh toán",
    "credit_days": "số ngày được nợ",
    "exp_date": "ngày chi",
    "exp_cat": "loại chi phí",
    "exp_amount": "số tiền chi",
    "ret_id": "mã phiếu trả hàng",
    "ret_ts": "thời điểm trả hàng",
    "refund": "tiền hoàn trả",
    "reason": "lý do trả hàng",
    "shift_id": "mã ca làm",
    "promo": "mã khuyến mãi",
}
ROLES = {
    "lines": "dòng bán: mỗi dòng một mặt hàng trong một đơn (có thể lặp lại thông tin đơn)",
    "orders": "mỗi dòng một đơn bán / một phiếu",
    "products": "danh mục hàng",
    "customers": "danh sách khách",
    "units": "bảng quy đổi đơn vị",
    "payments": "mỗi dòng một lần khách trả tiền",
    "purchases": "phiếu nhập hoặc dòng nhập hàng",
    "stock": "tồn kho cuối kỳ",
    "ledger": "thẻ kho",
    "prices": "lịch sử giá bán",
    "lots": "lô hàng và hạn dùng",
    "lot_sales": "bán theo lô",
    "drugs": "thông tin thuốc",
    "deliveries": "các đợt giao hàng",
    "terms": "hạn nợ của đơn",
    "expenses": "sổ chi phí",
    "returns": "phiếu trả hàng",
    "staff": "danh sách nhân viên / thợ",
    "suppliers": "danh sách nhà cung cấp",
    "other": "không thuộc loại nào ở trên",
}
SYSTEM = (
    "Bạn ánh xạ cột của các bảng dữ liệu bán hàng sang trường chuẩn. Dựa vào README, tên cột và giá trị mẫu.\n"
    "Chỉ trả về JSON dạng {\"tables\": {\"<tên bảng>\": {\"role\": \"<vai trò>\", \"cols\": {\"<tên cột>\": \"<trường>\"}}}}.\n"
    "- Chỉ dùng trường và vai trò trong danh sách. Cột không khớp trường nào thì ghi null.\n"
    "- Chép tên bảng và tên cột y nguyên. Mỗi trường dùng tối đa một lần trong một bảng.\n"
    "- Không chắc thì ghi null: ánh xạ sai gây hại hơn bỏ trống.\n\n"
    "Trường:\n" + "\n".join(f"- {k}: {v}" for k, v in FIELDS.items()) + "\n\n"
    "Vai trò bảng:\n" + "\n".join(f"- {k}: {v}" for k, v in ROLES.items())
)


def table_card(name, table, spec=None, values=4):
    """A few lines describing one table: rows, each column's kind, sample values and guessed field."""
    out = [f"### {name} ({len(table.df)} dòng" + (f", vai trò đoán: {spec['role']})" if spec else ")")]
    for col, kind in table.kinds.items():
        seen = table.raw[col][table.raw[col] != ""]
        few = seen.nunique() <= 12  # a short closed list (status, unit, payment): show it whole
        sample = list(seen.drop_duplicates().head(12 if few else values))
        field = f" -> {spec['cols'][col]}" if spec and col in spec["cols"] else (" -> ?" if spec else "")
        out.append(f"- {col} [{kind}]{field}: {', '.join(str(v)[:40] for v in sample)}")
    return "\n".join(out)


def needs_model(manifest):
    """True when the rules left the sales data unusable or left columns of an undescribed table unmapped."""
    tables = manifest["tables"].values()
    sales = [t for t in tables if t["role"] in ("lines", "orders")]
    fields = {f for t in sales for f in t["cols"].values()}
    core = bool(sales) and bool(fields & {"date", "ts"}) and bool(fields & {"amount", "order_goods"}) and "status" in fields
    return not core or any(t["unknown"] and not t.get("documented") for t in tables)


def parse_patch(text, tables):
    """The model's JSON -> a clean patch. Unknown tables, columns, fields and roles are dropped silently."""
    raw, dec = {}, json.JSONDecoder()
    for m in reversed(list(re.finditer(r'\{\s*"tables"', text or ""))):  # the last one: a model may think aloud first
        try:
            raw = dec.raw_decode(text, m.start())[0]["tables"]
            break
        except (ValueError, KeyError, TypeError):
            continue
    raw = raw if isinstance(raw, dict) else {}
    for name, spec in list(raw.items()):  # {"other": {"cols": {"SoSuaXe.Ngày": ...}}}: file each column under its table
        if name in tables or not isinstance(spec, dict):
            continue
        for col, field in (spec.get("cols") or {}).items():
            t, _, c = col.rpartition(".")
            if t in tables:
                raw.setdefault(t, {}).setdefault("cols", {}).setdefault(c, field)
    patch = {}
    for name, spec in raw.items():
        if name not in tables or not isinstance(spec, dict):
            continue
        cols, used = {}, set()
        for col, field in (spec.get("cols") or {}).items():
            if col in tables[name].kinds and (field is None or (field in FIELDS and field not in used)):
                cols[col] = field
                used.add(field)
        patch[name] = {"cols": cols, **({"role": spec["role"]} if spec.get("role") in ROLES else {})}
    return patch


def complete_manifest(tables, readme, manifest, ask):
    """Ask the model to fill in the manifest. -> (manifest, raw reply) ; the manifest is unchanged on a bad reply."""
    cards = "\n\n".join(table_card(n, tables[n], manifest["tables"][n]) for n in tables)
    user = f"## README\n{readme['text']}\n\n## Bảng (\"-> trường\" là phần đã đoán, \"-> ?\" là chưa biết)\n{cards}"
    # A filled-in frame: a 9B model answering without thinking keeps the exact table and column names this way.
    frame = {n: {"role": t["role"] if t["role"] != "other" else None, "cols": dict.fromkeys(t["unknown"])}
             for n, t in manifest["tables"].items() if t["unknown"] or t["role"] == "other"}  # fmt: skip
    user += ("\n\n## Trả lời\nChép đúng khung JSON dưới đây (giữ nguyên tên bảng, tên cột), thay null bằng trường hoặc "
             "vai trò nếu chắc:\n```json\n" + json.dumps({"tables": frame}, ensure_ascii=False, indent=1) + "\n```")
    reply = ask([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    for name, spec in parse_patch(reply, tables).items():
        t = manifest["tables"][name]
        # The model only fills gaps: a column the rules already understood keeps its field, a table keeps its role
        # unless the rules found none. A 9B model's guess is worth less than a lexicon match.
        new = {c: f for c, f in spec["cols"].items() if c not in t["cols"] and f not in t["cols"].values()}
        t["cols"] = {**t["cols"], **{c: f for c, f in new.items() if f}}
        # The fields now known settle the role (a "Tiền thu" column makes a payments table, not orders); a table
        # with sales money or quantities stays a sales table whatever one extra column suggests.
        derived, fields = role_of(t["cols"].values()), set(t["cols"].values())
        sales = t["role"] in ("lines", "orders") and fields & {"amount", "order_goods", "order_total", "qty"}
        strong = derived in {r for r, _ in ROLE_RULES}  # a field only that kind of table has; "has a name" is not
        if strong and t["role"] not in ("suppliers", "staff") and not sales:
            t["role"] = derived
        elif t["role"] == "other":
            t["role"] = spec.get("role", t["role"])
        if t["role"] == "other" and {"date", "ts"} & fields and {"order_total", "order_goods"} & fields:
            t["role"] = "orders"  # a dated book of whole-sale totals, whatever role the model forgot to give
        t["unknown"] = [c for c in t["unknown"] if c not in spec["cols"]]  # looked at, even if set to null
    return manifest, reply
