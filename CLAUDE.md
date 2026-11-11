# DataMate (Zalo AI Challenge 2026)

Agent trả lời câu hỏi tiếng Việt trên dữ liệu bán hàng của hộ kinh doanh nhỏ (CSV / XLSX / SQLite). Đề bài: `competition/` (ảnh `image*.png` chứa bảng luật chấm và công thức PA).

## Luật thi quan trọng
- Điểm PA: đúng +1, **sai −1**, abstain/thiếu 0 → không chắc thì `{"type":"abstain"}`.
- Track SLM: tổng tham số mọi model ≤ 15B, weights công bố trước 26/10/2026. Chạy **offline** trong Docker, 2×RTX 5090, ≤ 6h cho ~500 câu private (kể cả load model). Phải tái lập: |PA upload − PA re-run| ≤ 0.03 → giải mã deterministic, log từng câu vào `/result/logs/`.
- Cấm hard-code theo id câu / business_id. Không train trên public/private test.
- `predict.sh` đọc `/data`, ghi `/result/submission.jsonl`. `/data` read-only.
- Log mỗi câu bắt buộc có `id, answer, elapsed_ms, thinking` (nguyên văn, `null` nếu không gọi model), câu giải bằng code thêm `code, output`. Image `.tar` ≤ 100 GB, phải kèm `/code/training_code/`. Chi tiết: `competition/docker_guideline.txt`.
- Kế hoạch triển khai và kiến trúc: `PLAN.md`. Hạn nộp Docker 23:59 16/11/2026.
- Private test có **ngành mới** → không được dựa vào schema cố định của từng shop.

## Dữ liệu
- `data/train/train/`: 30 shop (`biz_101..130`), 3000 câu, có `answers.jsonl` (kèm `alias` = mã hàng/khách; list/table thì alias theo từng phần tử/dòng).
- `data/public_test/public_test/`: 14 shop (`dev_001..014`), 1000 câu, không có đáp án. Có ngành không có trong train (sửa xe `dev_012`, điện thoại `dev_014`, mẹ & bé...).
- Mỗi split có `rulebook.md` **riêng** (public test thêm luật ngành, luật khách trung thành, giờ cao điểm...) → agent phải đọc rulebook của split đang chạy. `calendar.csv` dùng cho Tết âm lịch.
- Phân bố type (train): number 1884, percent 578, list 193, date 145, string 88, unanswerable 70, boolean 23, table 19.
- Dữ liệu cố ý bẩn: ngày lẫn định dạng (`01/07/2025`, `1 thg 7 2025`), tiền dạng chuỗi (`560,000`, `270.000đ`), tên thợ/khách viết nhiều kiểu (`A.Đức`, `a Đức`), xlsx có dòng tiêu đề báo cáo trước header, nhiều sheet theo tháng, file tách theo tháng/năm, ô trống ở dòng sau của cùng đơn (kế thừa dòng đầu), dòng trùng do lỗi xuất file. README mỗi shop chất lượng không đều.

## Công cụ
- Chấm điểm: `python scripts/score.py <pred.jsonl> [--questions subset.jsonl]` (luật khớp theo Evaluation.md). Self-test: `python scripts/test_score.py`. Slash command: `/eval`.
- Loader: `from src.load import load_shop` → `{tên bảng: Table}` (`.df` đã ép kiểu ngày/số, `.raw` nguyên văn, `.dropped` dòng tổng/ghi chú bị loại). Báo cáo: `python -X utf8 -m src.load <thư mục split hoặc shop>` (44 shop ~2,5 phút). Loader **không** điền ô trống kế thừa và **không** bỏ dòng trùng: việc đó thuộc lớp schema. Test: `python -m pytest tests -q`.
- Lớp ngữ nghĩa: `src/schema.py` (README → manifest cột/vai trò bảng), `src/shop.py` (`open_shop` → `lines`/`orders`/`products` chuẩn: điền ô trống, bỏ dòng trùng, 4 trạng thái đơn), `src/period.py` (kỳ thời gian theo rulebook §2), `src/metrics.py` (`Metrics(shop, as_of, lunar)`: doanh thu, đơn, huỷ, số lượng, xếp hạng...).
- Đo lớp ngữ nghĩa: `python -X utf8 -m training_code.bank [biz_105 ...]` sinh chương trình ứng viên cho từng câu train, giữ cái ra đúng đáp án → `training_code/data/bank.jsonl` (ngân hàng ví dụ) và `missed.jsonl` (câu trượt). ~2,5 phút cho 30 shop. Mẫu câu hỏi trong file này **chỉ để dựng dữ liệu**, không dùng lúc thi.
- Vòng chạy: `python -X utf8 -m src.agent --data <thư mục split> --out <dir> --llm URL[,URL]` (model viết code trên `m`, `P`, `T`; ghi `submission.jsonl`, `logs/log.jsonl`, `logs/schema.jsonl`). Không có model thì `--oracle --manifests training_code/manifests` phát lại ngân hàng ví dụ trên train (PA 0,974, ~4 phút) để kiểm hồi quy. `src/mapper.py`: model hoàn thiện manifest khi luật không đọc được schema (README im lặng, ngành mới). `training_code/manifests/*.json` là manifest viết tay cho shop train, **chỉ dùng cho dev và lexicon**, `predict.sh` không bao giờ truyền `--manifests`.
- Chạy trên GPU: `python scripts/pack.py` → `datamate.zip` → tải lên Drive → `notebooks/colab_run.ipynb`. Docker: `Dockerfile` + `predict.sh` (mỗi GPU một vLLM server, cần `models/model/`).
- MCP dự án (`.mcp.json`): `duckdb` (SQL trong RAM; `read_csv`, `read_xlsx` qua extension `excel`, `ATTACH '...sqlite' (TYPE sqlite)`), `hf-mcp-server` (tìm model/dataset/paper trên Hugging Face). `context7` ở cấp user: tra docs `trl`, `peft`, `unsloth`, `vllm` trước khi viết code dùng chúng.
- Notebook: sửa bằng NotebookEdit, chạy bằng `jupyter nbconvert --to notebook --execute --inplace <file>`.
- Hook: ruff tự chạy sau mỗi lần sửa `.py`; `git commit` bị chặn nếu `scripts/test_score.py` lỗi.
- Python 3.11 hệ thống ở `D:\Python311` (Scripts không có trong PATH → dùng `python -m uv`, `python -m ruff`). Terminal mặc định cp1252: đặt `PYTHONIOENCODING=utf-8` khi in tiếng Việt.
- Máy local: RTX 3050 Ti 4GB, torch bản CPU → chỉ prototype/đọc dữ liệu; train và chạy model thật trên máy GPU khác.
