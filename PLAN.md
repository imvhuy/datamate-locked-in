# PLAN — DataMate (lập 10/11/2026, hạn nộp Docker 23:59 16/11/2026)

## 1. Những gì dữ liệu thật cho thấy

Khảo sát 30 shop train + 14 shop public, 4000 câu hỏi, 2 rulebook:

1. **Dữ liệu sinh từ một bộ sinh chung**, mỗi shop là một "lớp da" khác nhau của cùng một mô hình ngầm
   (đơn, dòng hàng, khách, hàng, thanh toán, nhập, lô, bảng giá, chi phí, tồn). Da đã gặp: CSV phẳng,
   xuất phần mềm kiểu `DanhSachHoaDon*`, kiểu `So_chi_tiet_ban_hang`, sổ tay Excel mỗi tháng một sheet,
   file tách theo tháng/năm, SQLite. Tên cột bị xáo (`Đối tác`, `Kết quả`, `Nội dung`, `Trị giá`...).
2. **Bẩn có quy luật, hữu hạn**: ngày (`45717` serial Excel, `01/07/2025`, `1 thg 7 2025`, ISO),
   tiền (`560,000`, `270.000đ`, `25k`, `19tr990`, `20,1tr`, `1,500.0000`), số lượng dính đơn vị (`5 chai`),
   header 2 dòng / dòng tiêu đề báo cáo, ô trống kế thừa dòng đầu đơn, dòng trùng, tên người viết nhiều kiểu
   (`A.Đức`/`a Đức`, tên dính SĐT), tên hàng ghi tắt có bảng tra.
3. **Câu hỏi là mẫu × nhiễu diễn đạt** (không dấu, teencode, gõ telex lỗi `dduwowcj`, phương ngữ `mô/rứa/ni`, emoji).
   Số ý định hữu hạn và **đều được định nghĩa trong rulebook**: doanh thu ~40%, tồn kho ~15%, huỷ ~8%, nợ ~6%,
   còn lại là số đơn, khách, giá, xếp hạng, nhập, nhóm hàng, ca, kê đơn, hạn dùng, lãi.
4. **Rulebook chính là đặc tả metric**, và nó đổi theo split: public thêm ~25 luật (khách sộp, giờ cao điểm,
   NCC chính...) và 3 ngành mới (mẹ & bé, sửa xe, điện thoại), bỏ 3 luật của train. Private sẽ lại khác.
5. **Ngành mới = schema mới hẳn** (sổ sửa xe: mỗi dòng một phiếu, ô `Làm gì` chứa nhiều việc; điện thoại: sổ góp,
   thu máy cũ, IMEI). Nhưng phần lớn câu hỏi trên ngành mới vẫn là mẫu chung (doanh thu hôm qua, số đơn tháng...).
6. **Unanswerable (2.3%) có 3 loại**, hai loại đầu kiểm được bằng code: kỳ nằm ngoài khoảng ngày có dữ liệu;
   shop không ghi loại thông tin đó (nợ, tồn, giá vốn); cần hiểu biết bên ngoài (liều thuốc, tay nghề, địa chỉ).
7. **Chấm khớp tuyệt đối, sai −1** → LLM không được tự tính số; mọi con số phải ra từ code. Chỉ trả lời khi
   P(đúng) > 0.5.
8. **Leaderboard public**: 19/20 đội đầu có SLM = 1.0. Nhãn SLM là tự khai, kết quả public không qua re-run
   → không phải thước đo. Cuộc đua thật là private: **ngành mới + rulebook mới + chạy lại trong Docker**.
   Hoà điểm thì xét tổng thời gian inference.

## 2. Giải pháp: lớp ngữ nghĩa + LLM viết code ngắn trên lớp đó

Ngoài thực tế, không hệ BI nào cho LLM viết SQL thẳng lên file Excel bẩn. Họ làm: ETL sạch → lớp ngữ nghĩa
(metric định nghĩa một lần) → câu hỏi dịch sang truy vấn trên lớp đó → từ chối khi không đủ dữ liệu.
Đề bài này đúng hình dạng đó: rulebook = định nghĩa metric.

Ba phương án đã cân:

| Phương án | Trên dạng đã gặp | Trên ngành mới | Kết luận |
|---|---|---|---|
| A. Agent text-to-pandas thuần trên file thô | thấp (bẩn + ~80 luật tinh vi, khớp tuyệt đối) | trung bình | loại: PA khó dương cao với model ≤15B |
| B. Bắt mẫu câu hỏi + hàm cứng, không LLM | rất cao | gần 0 | loại: gãy đúng chỗ private chấm |
| **C. Lớp ngữ nghĩa xác định + LLM sinh code gọi helper** | cao | khá | **chọn** |

### Kiến trúc C (một cơ chế duy nhất, suy giảm mềm)

```
/data ──► [1] Loader xác định ──► bảng sạch T[...]  (đúng kiểu ngày/tiền/số lượng)
              │
              ▼
          [2] Ánh xạ schema (heuristic + LLM, 1 lần/shop) ──► manifest ──► view chuẩn S.lines, S.orders,
              │                                              S.payments, S.purchases, S.lots, S.prices...
              ▼
câu hỏi ─► [3] Liên kết thực thể + truy hồi ví dụ/luật ─► [4] LLM viết code Python ngắn gọi helper
                                                              │
                                                              ▼
                                           [5] Chạy sandbox ─► [6] Kiểm chứng / abstain ─► submission + log
```

1. **Loader xác định** (không LLM): tìm dòng header, gộp header nhiều dòng, nối file tách (`_1`, `_2026`, theo tháng)
   và sheet theo tháng (tên sheet cho năm của ngày thiếu năm), parser ngày/tiền/số lượng, điền ô trống theo nhóm đơn,
   bỏ dòng trùng **trong cùng đơn**. Mọi thứ sau đều phụ thuộc lớp này → làm đầu tiên, test kỹ nhất.
2. **Ánh xạ schema → manifest** (JSON, sinh lúc chạy, không ship theo business_id): vai trò từng bảng, cột → trường
   chuẩn, giá trị trạng thái → 4 trạng thái của rulebook, nhãn khách lẻ, khoá nối, đơn vị cơ bản/hệ số quy đổi.
   Heuristic (từ điển đồng nghĩa + suy kiểu từ giá trị) chạy trước, LLM đọc README + profile cột chạy sau,
   rồi **kiểm bằng bất biến**: SL × đơn giá ≈ thành tiền, tổng dòng ≈ tổng đơn, tỉ lệ parse ngày > 99%.
   Bất biến hỏng → hạ độ tin cậy của mọi câu hỏi dùng cột đó.
3. **Helper = rulebook viết thành code**: `P.month(y,m)`, `P.this_month()`, `P.last_week()`, `P.tet(y)`,
   `P.same_days_prev_month()`; `revenue()`, `n_orders()`, `cancel_rate()`, `top_k()`, `debt_as_of()`,
   `stock_as_of()`, `gross_profit()`... Kỳ thời gian luôn do code tính từ `as_of_date`, không để LLM tính.
   `fmt.number/percent/date/list/table` lo làm tròn và format theo §9.
4. **LLM viết code ngắn** (giống Example 1 trong Docker guideline): prompt gồm manifest, tài liệu helper,
   thực thể đã liên kết (tên hàng/khách/nhóm khớp mờ không dấu với danh mục), **k ví dụ tương tự đã kiểm chứng**
   (BM25 trên câu hỏi bỏ dấu — không tốn tham số cho embedding) và **các dòng rulebook liên quan của split đang chạy**.
   Ý định quen → một dòng gọi helper. Ý định lạ/ngành mới → pandas trên `T[...]` sạch theo đúng luật ngành.
5. **Rulebook mới**: lúc khởi động diff từng dòng rulebook `/data` với 2 rulebook đã biết. Dòng giữ nguyên → helper
   đáng tin. Dòng sửa/mới → gắn nhãn "LUẬT MỚI", luôn đưa vào prompt của câu hỏi liên quan và cấm đường tắt helper cũ.
6. **Kiểm chứng và abstain**:
   - Unanswerable xác định: kỳ nằm hoàn toàn ngoài khoảng ngày dữ liệu; manifest thiếu năng lực cần (nợ, tồn, giá vốn).
     Loại "kiến thức ngoài" để LLM phán.
   - Sinh **2 chương trình độc lập** (bộ ví dụ khác nhau, temperature 0) → hai kết quả trùng thì trả lời;
     lệch thì theo bảng độ tin cậy, không đủ thì `abstain`.
   - Bảng độ tin cậy **hiệu chỉnh trên 3000 câu train**: theo (nhóm ý định × đồng thuận × trạng thái bất biến),
     chỉ trả lời nhóm có precision ≥ 0.6 (hoà vốn 0.5, chừa biên cho lệch phân phối).

### Ngân hàng ví dụ và fine-tune: giám sát yếu bằng thực thi

Với mỗi câu train, sinh nhiều chương trình ứng viên (mẫu viết tay + model thầy), **giữ chương trình có kết quả
trùng đáp án chuẩn**. Ra ~3000 cặp (câu hỏi → code đã kiểm chứng) mà không gán nhãn tay. Dùng cho: (a) ví dụ truy hồi,
(b) dữ liệu LoRA, (c) bộ test hồi quy của helper. Tăng cường nhiễu (bỏ dấu, teencode, lỗi telex) sinh bằng script.
Guideline cho phép dữ liệu sinh bằng API nếu lưu prompt/response → lưu ngay từ đầu.

Fine-tune là **tuỳ chọn** (ngày 5): chỉ làm nếu model gốc + truy hồi chưa đạt mục tiêu. Kỹ thuật (loader, helper,
kiểm chứng) có ROI cao hơn trong 6 ngày.

### Model

| Ứng viên | Tham số | Ghi chú |
|---|---|---|
| Qwen/Qwen3.5-9B | 9.65B | Apache-2.0, 03/2026. bf16 ~19 GB → **mỗi GPU một bản sao**, khỏi tensor-parallel/NCCL |
| google/gemma-4-12B-it | 11.96B | Apache-2.0, 05/2026. Cần FP8 để chừa chỗ KV cache |
| Qwen/Qwen3-14B | 14.77B | Model BTC đã thử trên base image khuyến nghị → **phương án an toàn**; bf16 không vừa 1 GPU |

Chọn bằng bake-off trên 300 câu train (độ đúng code sinh ra, tốc độ), không chọn theo tiếng tăm. Hai model 2026
cần vLLM mới hơn base image `v0.10.1.1` → phải thử sớm qua "Test Solution". Chỉ dùng **một** model, không embedding.

### Tái lập và thời gian
- temperature 0, seed cố định, thứ tự câu cố định, batch tĩnh. Đáp án ra từ code nên lệch vài token hiếm khi đổi kết quả.
  Chạy 2 lần và so `submission.jsonl` trước khi nộp.
- Ghi từng câu ngay khi xong (append + flush). Log JSONL: `id, answer, elapsed_ms, thinking` (nguyên văn), `code, output`.
- Ngân sách 6h cho 500 câu = 43 s/câu; mục tiêu < 45 phút tổng để thắng tie-break. Có watchdog: quá 5h thì abstain phần còn lại.

## 3. Cách đo (để không tự lừa mình)

| Thước đo | Mô phỏng | Mục tiêu |
|---|---|---|
| PA train, helper + chương trình chuẩn | trần của lớp ngữ nghĩa | ≥ 0.97 |
| PA train end-to-end | shop dạng quen | ≥ 0.90 |
| PA bỏ-một-ngành (ẩn hẳn nhà thuốc / VLXD khỏi ví dụ, từ điển, LoRA) | ngành mới ở private | ≥ 0.50 |
| PA public, chỉ nộp nguyên bản do code chạy ra (không nộp tách nhóm câu: Rule 6 cấm thu thập đáp án test bằng mọi cách) | ngành mới thật: dev_012/013/014 | ≥ 0.80 tổng |
| Lệch giữa 2 lần chạy | Rule 8 | 0 câu, tối đa vài câu |

Public test **không dùng để train và không tự gán nhãn** (Rule 6); chỉ đọc schema và lấy phản hồi qua leaderboard
(5 lượt/ngày → ~30 lượt còn lại, mỗi lượt là một thí nghiệm có chủ đích).

## 4. Lịch 6 ngày

| Ngày | Việc | Xong khi |
|---|---|---|
| **10/11** | Loader + parser + profile cho cả 44 shop. Harness chạy/chấm theo shop. Chốt máy GPU, dựng vLLM. | 44 shop load không lỗi; bảng thống kê tỉ lệ parse |
| **11/11** | Manifest + view chuẩn. Helper nhóm doanh thu/đơn/huỷ/xếp hạng/nhóm/ca/khách. Bake-off model. | ≥ 60% câu train đúng bằng chương trình chuẩn |
| **12/11** | Helper còn lại: nợ, tồn, nhập, giá, lãi, lô/hạn dùng, Tết. Ngân hàng ví dụ. Vòng LLM end-to-end. Nộp public lần 1. | PA train (chương trình chuẩn) ≥ 0.95; có điểm public |
| **13/11** | Ngành mới: đường pandas trên `T`, diff rulebook, unanswerable, hiệu chỉnh abstain. **Docker v0 → Test Solution** (1 lượt/ngày). | Image chạy được trên máy BTC |
| **14/11** | Đo bỏ-một-ngành, sửa lỗi theo nhóm, tối ưu tốc độ, kiểm tái lập. LoRA nếu cần. | Đạt mục tiêu §3 hoặc biết rõ thiếu ở đâu |
| **15/11** | **Đóng băng code.** `training_code/`, `data_manifest.json`, README khai model. Build image cuối, `docker save`, md5, upload, tải lại kiểm md5. Test Solution bản cuối. | Link + md5 sẵn sàng |
| **16/11** | Dự phòng. Chỉ sửa lỗi chặn. Nộp trước 18:00, không đợi 23:59. | Đã nộp |

Thứ tự cắt khi trễ: LoRA → kiểm chứng 2 chương trình (còn 1 + abstain theo bất biến) → helper ngành hẹp (lô, Tết).
Không cắt: loader, format log, chạy thử Docker trên máy BTC.

## 5. Rủi ro

| Rủi ro | Xử lý |
|---|---|
| Không có máy build/chạy Docker với GPU ≥ 24 GB (máy local 4 GB) | Chốt trong ngày 10/11. Lưu ý nhiều dịch vụ thuê GPU dạng container không cho `docker build` → cần VM thật |
| Upload image 30–50 GB lên Google Drive | Upload từ máy cloud (rclone), không từ mạng nhà; thử từ 13/11 |
| vLLM mới không chạy trên máy BTC | Test Solution từ 13/11; dự phòng Qwen3-14B trên base image khuyến nghị |
| Overfit 44 shop đã thấy | Manifest sinh lúc chạy; đo bỏ-một-ngành; không có nhánh nào theo tên file hay business_id |
| Bị coi là hard-code | Mọi quyết định đi qua LLM hoặc luật tổng quát; log đủ `thinking` + `code` |
| Code LLM sinh ra treo/lỗi | subprocess có timeout, tối đa 2 lần sửa, sau đó abstain |

## 6. Cấu trúc repo dự kiến

```
src/load.py      loader + parser            src/agent.py   prompt, truy hồi, sinh code, kiểm chứng
src/schema.py    manifest + view chuẩn      src/run.py     vòng chạy chính, log, watchdog
src/metrics.py   helper theo rulebook       predict.sh, Dockerfile, README.md
src/period.py    kỳ thời gian, lịch âm      training_code/ ngân hàng ví dụ, script sinh, LoRA, data_manifest.json
tests/           parser, period, hồi quy 3000 câu train
```

## 7. Tiến độ (cập nhật 11/11)

| Hạng mục | Trạng thái |
|---|---|
| Loader + parser | Xong. 44 shop, không còn ô parse lỗi. |
| Manifest từ README + lexicon tên cột | Xong. 26/30 shop train và 12/14 shop public đọc được bằng luật thuần. |
| View chuẩn + metric theo rulebook | Xong cho mọi nhóm câu hỏi của train: doanh thu, đơn, huỷ, số lượng, xếp hạng, nhóm, ca, giá, lãi, nhập, tồn, nợ, lô, giao hàng, thuốc, Tết. |
| Ngân hàng ví dụ (`training_code/data/bank.jsonl`) | 2.922/3.000 câu train có chương trình đã kiểm chứng, gồm đủ 70 câu không trả lời được. |
| Toàn tuyến ở chế độ oracle | PA 0,974 trên train, 0 câu sai (78 câu bỏ trống do mẫu chưa bắt được lỗi gõ). |
| Model ánh xạ schema (`src/mapper.py`) | Đã viết và test bằng model giả. **Chưa chạy với model thật.** |
| Vòng chạy model (`src/agent.py`) | Đã viết, đã chạy thông trên 14 shop public bằng model giả. **Chưa có số đo với model thật.** |
| Đóng gói | `predict.sh`, `Dockerfile`, `requirements.txt`, `notebooks/colab_run.ipynb` đã có. Chưa build image, chưa có `README.md` của lời giải, chưa có `data_manifest.json`. |

Việc tiếp theo, theo thứ tự: (1) chạy notebook Colab trên 300 câu train để có số đo đầu tiên và chọn model;
(2) sửa prompt theo lỗi thật; (3) chạy public và nộp leaderboard; (4) kiểm chứng 2 chương trình + hiệu chỉnh abstain;
(5) build Docker và gửi Test Solution.

Lỗ hổng đã biết: tồn kho tại ngày trước ngày cuối dữ liệu chỉ tính được khi shop có thẻ kho; ví dụ few-shot cho
câu không trả lời được đôi khi là chương trình báo thiếu dữ liệu vì lý do khác với lý do thật; chưa có luật riêng
cho ngành mới của public (sửa xe, điện thoại, mẹ & bé), các câu đó dựa hoàn toàn vào model đọc rulebook và bảng `T`.

### Quy ước đo (từ 11/11)

- `training_code/splits/dev300.jsonl` (300 câu đầu train): dò lỗi, được đọc từng câu sai và sửa code theo. Điểm của nó lạc quan.
- `training_code/splits/holdout300.jsonl` (300 câu cuối, không trùng dev): **chỉ báo điểm**. Không đọc từng câu sai để sửa code;
  nếu buộc phải đọc thì rút holdout mới và ghi lại ở đây.
- Điểm train nói chung là trần trên: 30 shop train dùng chung mẫu câu, private có ngành mới. Phép đo khách quan duy nhất là public leaderboard.

| Ngày | Model | Tập | PA | Đúng / Sai / Bỏ trống | Ghi chú |
|---|---|---|---|---|---|
| 11/11 | Qwen3.5-9B, A100 | dev300 | 0,857 | 265 / 8 / 27 | lần đầu |
| 11/11 | Qwen3.5-9B, A100 | dev300 | 0,867 | 270 / 10 / 20 | sau sửa schema SQLite, prompt, `answer =` |
| 11/11 | Qwen3.5-9B, A100 | **holdout300** | **0,950** | 287 / 2 / 11 | + bước kiểm tra chương trình, thử lại 1 lần; 701 s (2,3 s/câu). Yếu nhất: câu không trả lời được 6/15 đúng, 8 bỏ trống |
| 11/11 | Qwen3.5-9B, A100 | public 1000 | ? (chưa nộp) | — / — / 188 bỏ trống | 2.915 s. 112 bỏ trống do suy nghĩ quá 6.144 token nên không kịp trả lời; trường `thinking` trong log rỗng vì không tách được `</think>`. Bỏ trống dồn vào dev_012 sửa xe 38/50, dev_014 điện thoại 38/63, dev_003 42/129 |
| 11/11 | Qwen3.5-9B, A100 | public 1000 | **0,548 (leaderboard, nộp 15:42)** | ~714 / ~166 / 120 (suy từ PA) | 1.945 s. Mapper trước đó chưa từng chạy được (model suy nghĩ quá 8.192 token, không ra JSON); thêm lượt hỏi lại không suy nghĩ, vai trò bảng tính lại sau mapper, luật README về mã đơn. dev_003 42→15, dev_014 38→10; dev_012 vẫn 40/50 vì JSON nằm giữa phần nghĩ thành tiếng (đã sửa `parse_patch`, chưa đo). Kết quả ở Drive `zaloai/public_run2` |
| 11/11 | Qwen3.5-9B, A100 | holdout300 | 0,957 | 288 / 1 / 11 | Mapper đã chạy được (8 shop train dùng nó). 1.051 s |
| 11/11 | Qwen3.5-9B, A100 | public 1000 | ? (chưa nộp) | — / — / 91 bỏ trống, 63 không trả lời được | 1.902 s. Mapper có khung JSON điền sẵn; dev_012 bỏ trống 42→14. Nhưng phần lớn "không trả lời được" ở shop ngành mới là sai (KeyError group/pay/staff vì schema hiểu chưa hết) → từ bản sau, shop do model ánh xạ không tin NoData. Holdout cùng bản: 0,943 (285/2/13), do biz_123 `pos.nhan_vien` thành `staff` không cột. Kết quả ở `zaloai/public_run3` |
