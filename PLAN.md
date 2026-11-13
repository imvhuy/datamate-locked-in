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

**12/11: chuyển sang Qwen/Qwen3.5-9B** (người dùng chọn vì 14B quá chậm). Hợp lệ: Apache-2.0, công bố 02/2026,
~9,65B tham số. Cần vLLM ≥ 0.17 (`vllm/vllm-openai:v0.17.1`: CUDA 12.9.1, Python 3.12, arch list có `12.0`); vLLM
0.17.1 chỉ có wheel cu129/cu130, không có cu128. **Rủi ro:** guideline chỉ cam kết driver R570, CUDA 12.9 cần driver
≥ 575 → đặt `NVIDIA_DISABLE_REQUIRE=1` trong Dockerfile, hỏi BTC driver máy chấm hoặc dùng Test Solution nếu có.
Colab đo trước bằng `vllm==0.17.1` trong Python 3.12, server `--language-model-only`. Docker chưa đổi.
Đo 12/11 (A100): `uv pip install vllm==0.17.1` kéo **torch 2.10.0+cu128**, transformers 4.57.6; server lên sau 115 s.
holdout300 với giải mã lấy mẫu + `--votes 3`: **0,950 (287 / 2 / 11), 2.252 s** — y hệt bản greedy 1 lần hôm 11/11
(287 / 2 / 11, 701 s): biểu quyết không thêm gì ở câu quen thuộc, tốn gấp 3,2 lần thời gian.
hidden_rules cùng cấu hình: **0,272 (78 / 25 / 92), 4.290 s** — so với 0,010 (37 / 35 / 123) của Qwen3-14B greedy
1 lần: câu luật mới trả lời đúng 76% (78/103), hôm trước ~51%. Tốc độ A100, 16 luồng: 7,5 s/câu quen, 22 s/câu luật mới.
Ý cho Docker: vì vLLM 0.17.1 từ PyPI chạy với torch cu128, có thể build FROM `vllm/vllm-openai:v0.10.1.1` (CUDA 12.8,
BTC đã thử) rồi `pip install vllm==0.17.1` → runtime CUDA 12.8, khớp cam kết driver R570 (cần thử trên Colab trước).

~~Chốt 11/11: Qwen/Qwen3-14B~~, bf16, một server vLLM chia model trên 2 GPU (tensor parallel), đúng cấu hình BTC
đã chạy thử trên máy chấm với base image `vllm/vllm-openai:v0.10.1.1`. Lý do: Qwen3.5-9B có kiến trúc `qwen3_5`
(attention tuyến tính + phần ảnh) mà vLLM 0.10.1.1 không hỗ trợ; dùng nó phải đổi base image chưa ai thử trên RTX 5090,
và đội không có máy 5090 để kiểm. Colab chạy đúng `vllm==0.10.1.1` để thử luôn nền tảng. Chỉ dùng **một** model.

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
| vLLM mới không chạy trên máy BTC | Đã tránh: Qwen3-14B trên đúng base image khuyến nghị (BTC đã thử) |
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

### Hướng mới sau điểm public 0,548 (11/11)

Chẩn đoán (giả thuyết, chưa đo trực tiếp): phần quen thuộc vẫn đúng ~95% như holdout; điểm mất dồn vào khoảng 245 câu
public chạm **luật mới hoặc ngành mới** của rulebook public (khách trung thành, thợ chăm chỉ, giờ cao điểm, sửa xe,
điện thoại...). Helper và ví dụ được viết theo rulebook train, nên model chọn helper gần nhất và trả lời tự tin nhưng sai
định nghĩa. Đề viết rõ: agent phải "read and apply the rulebook of the split, not a copy of the training rulebook".

Cách xử lý, không phụ thuộc shop hay ngành cụ thể:
1. `src/rules.py`: tách rulebook của split thành từng dòng luật, BM25 lấy các dòng gần câu hỏi, trích nguyên văn vào prompt.
2. Mỗi helper ghi rõ luật nó cài đặt (`IMPLEMENTS` trong `src/metrics.py`, hiện ở API dạng `[rule: ...]`); model chỉ dùng
   helper khi đúng luật đó, còn lại tự viết pandas trên `m.sold` / `m.orders` / `T` theo lời luật. Prompt liệt kê cột của
   các view ("Sales views").
3. `--verify`: lần giải thứ hai không có ví dụ; hai đáp án không khớp thì bỏ trống.
4. Đo: `training_code/hidden_split.py` tạo `hidden_rules.jsonl` (195 câu train cần 13 helper) và chạy với `--hide` các
   helper đó, như thể luật của chúng là luật mới. Đáp án train chấm được từng câu, không đụng public hay holdout.

Không thăm dò leaderboard bằng file nộp tách nhóm câu (Rule 6). Public chỉ nộp bản do code chạy ra trọn vẹn.

**Đo 11/11 tối (Qwen3-14B, vLLM 0.10.1.1, Colab A100, Python 3.12 qua uv, transformers 4.55.4):**
- Server lên sau 140 s (BTC: 128 s). Chạy thử 3 câu holdout: 3/3 đúng.
- `hidden_rules` (195 câu, giấu 13 helper, giải 1 lần): **PA 0,010 = 37 đúng / 35 sai / 123 bỏ trống**, 3.070 s
  (trung vị 228 s/câu, 16 luồng; 144/195 câu phải thử lại).
- Model 14B **không** tự viết pandas theo lời luật khi thiếu helper: hoặc lấy helper gần nhất (hỏi "nhân viên bán giỏi
  nhất" → `top_customers`; hỏi tồn chẵn/lẻ → `top_products`), hoặc chép nguyên ví dụ mẫu trong SYSTEM (89 câu ở lần
  thử đầu). 44 câu nghĩ quá 15.000 ký tự. Bỏ trống: 56 do `check` chặn ví dụ chép, 38 sai định dạng, ~20 code lỗi.
- Thử nhận diện "câu dựa trên luật mới" bằng dòng luật BM25 gần nhất: gắn cờ 322/1000 câu public với rất nhiều nhầm
  ("có mấy đơn huỷ" → "Thu máy cũ") và chỉ bắt 84/195 câu giả lập → **không dùng được**.
- `--verify` (lần 2 không ví dụ phải khớp lần 1): **PA 0,026 = 6 / 1 / 188**, 2.519 s. Chặn gần hết câu sai nhưng mất
  gần hết câu đúng. Holdout lượt này bị dừng tay trước khi chạy.
- **Cả hai lượt chạy giải mã greedy (temperature 0) ở chế độ thinking, đúng điều model card Qwen3 cấm** ("DO NOT use
  greedy decoding... performance degradation and endless repetitions"): khớp triệu chứng 44 câu nghĩ >15k ký tự, câu
  "nghĩ" 1 ký tự, 89 câu chép ví dụ. Các số trên vì vậy đánh giá thấp model.

**Sửa 12/11 (A, B, E):**
- A: thinking dùng temperature 0.6 / top_p 0.95 / top_k 20 (không thinking: 0.7 / 0.8 / 20), seed cố định theo câu
  (`crc32(id) + 1000·k + lần thử`); lần thử lại vẫn thinking với seed khác, không tắt thinking (bản không thinking chép ví dụ).
- B: `--votes 3` (mặc định): giải 3 lần độc lập song song, trả lời khi ≥2 lần khớp (so bằng `score_one`), không thì bỏ
  trống. Thay cho `--verify`. `predict.sh` hạ `--workers` 16 → 8 vì mỗi câu gửi 3 yêu cầu.
- E: "không trả lời được" cần cả 3 lần cùng kết luận, trừ khi kỳ hỏi nằm hoàn toàn ngoài dữ liệu.
- Colab: Python 3.12 qua `uv` + `vllm==0.10.1.1` + `transformers>=4.55,<4.56` (Colab mặc định Python 3.13; transformers
  mới bỏ `all_special_tokens_extended`). Server lên sau 140 s. Notebook `notebooks/colab_run.ipynb` đã cập nhật.

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

### Public 0,593 (12/11) và bản sửa sau phân tích log
Qwen3.5-9B, `--votes 3`: 738 đúng / 145 sai / 117 bỏ. Chỉ 5 câu bỏ vì các lượt bất đồng; câu sai hầu hết là 2 lượt
cùng sai (lỗi hệ thống) → bỏ biểu quyết, mặc định `--votes 1`. Lỗi đã sửa (đo bằng log, không cần đáp án):
- 276 lượt giải trả lời trống, code nằm cuối phần thinking → `from_thinking` lấy khối code + type cuối (không lấy khi bị cắt).
- Sổ bán ghi tắt tên hàng (dev_011/014, sheet `Ghi tắt`) → `_book_names` đổi sang tên đầy đủ (giữ `book_name`); cột `Loại` → `group`.
- Chọn danh mục hàng theo độ khớp với sổ bán (dev_014 nhận nhầm sổ trả góp); không có danh mục thì dùng bảng tồn kho (dev_007/008: giá vốn, đơn vị).
- Hàng chỉ có trong sổ bán (Voucher, Phí ship) vẫn là hàng của shop.
- Nợ ghi bằng chữ trong Note (dev_008: "còn thiếu 73 nghìn") → cột `debt`.
- Tên thợ gọi tắt ("A.Đức", "a Đức") → họ tên trong danh sách thợ (dev_012).
- Shop chỉ có bảng đơn: `m.sold` dùng bảng đơn (doanh thu theo thợ dev_012).
- Cột ngày của bảng chi phí/giá/giao/trả hàng đặt đúng trường kể cả khi model map (`fit_fields`, dev_003 lãi ròng).
- So sánh cột ngày với `P.x()` (một cặp ngày) bị chặn trước khi chạy, gợi ý `m.rows`; prompt: luôn lọc kỳ của T bằng `m.rows`.
- Colab: giải nén bằng Python (unzip làm hỏng tên file tiếng Việt của dev_001).

### Public 0,611 (12/11, giải 1 lần + bản sửa) → chế độ agent nhiều bước (`--steps N`)
Mỗi câu đang dùng ~20 s trong ngân sách ~10 phút/câu, và model viết code mà không nhìn dữ liệu. `--steps 8`: mỗi câu là
một notebook (`src/notebook.py`) giữ biến giữa các ô; trả lời không có dòng type là ô nháp (chạy, trả lại phần in ra và
giá trị dòng cuối, cắt 2.500 ký tự); có dòng type là đáp án. Đáp án tính bằng pandas (dùng T, m.sold/orders/rows,
groupby, nhiều dòng) được hỏi lại một lần để đối chiếu từng mệnh đề của luật; đáp án chỉ gọi hàm của m chốt ngay.
Hết bước mà chưa có đáp án chạy được thì bỏ. Ngữ cảnh model nâng lên 65.536 token. Đo: hidden_rules (mốc 0,272) rồi
holdout300 (mốc 0,950); chỉ bật cho public/Docker nếu hidden tăng rõ mà holdout không tụt.

### Đo agent nhiều bước 12/11: hidden_rules 0,118 (95 / 72 / 28), 3.575 s — tệ hơn mốc 0,272
Trả lời thêm 64 câu so với mốc, chỉ 17 đúng. Phân tích log: 149/195 câu model không mở ô nháp nào. Các lỗi chính:
- đếm dòng thay vì đếm đợt (n_deliveries, sai 16/16);
- tiền không làm tròn (5 câu);
- model tự kết luận "không có dữ liệu" (sai 10/12);
- ô xem dữ liệu viết dưới dòng type bị coi là đáp án ("running it failed: None", 15 câu);
- mẫu số tỉ lệ huỷ.

Sửa trong `explore`:
- ô có `print` mà không gán `answer` thì chạy như ô nháp;
- bước tự kiểm tra cho model xem các bảng mà code đọc (số dòng, số giá trị khác nhau mỗi cột, 3 dòng đầu), kèm câu nhắc "đếm khoá phân biệt, không đếm dòng";
- số tính bằng pandas chưa làm tròn thì nhắc một lần;
- model tự kết luận "không có dữ liệu" (qua pandas hoặc no_data) thì bị hỏi lại một lần, vẫn khẳng định thì bỏ trống;
- `--confirm`: đáp án tính bằng pandas phải được lần giải thứ hai (seed khác) khớp. Log giữ cả hai lần giải, nên đo được cả có và không có cổng này từ một lần chạy.

### 12/11 tối: bỏ chế độ nhiều bước, sửa lớp dữ liệu theo rulebook public (chạy public một lần, không đo thêm)
Đã gỡ hẳn chế độ nhiều bước (explore, src/notebook.py, --steps, --confirm). Lý do: hidden 0,118 so với 0,272 của chế độ một lượt; 149/195 câu model không hề xem dữ liệu; các bản sửa chưa đo được, mà credit chỉ đủ một lượt chạy. Ngữ cảnh trở lại 32768 như lượt public 0,611.

Kiểm tự động lớp dữ liệu trên 14 shop public (không dùng nhãn), tìm ra và sửa:
- **dev_002, topping ghi chung ô với món** (1.095 dòng): tách thành dòng món và dòng topping theo luật và README (số phần đứng trước tên, tiền trong ngoặc); tổng tiền giữ nguyên.
- **dev_013, dòng Voucher, Phí ship, Dùng N điểm**: §1 nói doanh thu không trừ voucher và không gồm phí ship. Các dòng này trước đây bị cộng vào doanh thu và tính là mặt hàng. Giờ cột `charge` loại chúng khỏi `sold()`, `order_goods` và danh sách mặt hàng; hàm `m.charges(p, kind)` cộng chúng thành số dương.
- **dev_011 và dev_014, tên khách kèm số điện thoại viết nhiều kiểu**: khách bị tách gần gấp đôi (1.185 thay vì 692). `cust_key` gộp các nhóm chữ số bị tách bởi dấu cách.
- **dev_012, sổ sửa xe**: khách là xe, xác định theo biển số (`plate_of`); xếp hạng trả về biển số.

Thêm hàm cho các luật mới trong rulebook public: khách trung thành, khách sộp, khách hay đổi ý, mặt hàng ưa chuộng, thợ chăm chỉ, ngày đắt khách, giờ cao điểm, trung bình theo thứ, nhà cung cấp chính, tiền voucher/điểm/ship. Các hàm này nằm trong `OPTIONAL`, chỉ hiện trong prompt khi rulebook của split có đúng tên luật đó. Trên train chúng bị ẩn hết, nên prompt train không đổi. Oracle train vẫn 0,9757 (2927 / 0 / 73). Đã chạy thử cả quy trình public bằng model giả: 1000/1000 câu, không crash.

### 13/11: public v3 = **0,643** (từ 0,611)
Phần tăng chủ yếu ở dev_013 (doanh thu bỏ voucher và ship) và ở sửa xe/điện thoại (biển số, gộp khách). Đọc log thấy:
- 16 câu viết tháng tắt (`T7/2025`, `Th 3/25`, `5/26`; train không có câu nào kiểu này) bị model đọc thành thứ 7, quý hoặc ngày.
- Vài câu ra 0 hoặc tỉ trọng âm.

Đã sửa (v4):
- `check()` báo lỗi khi câu hỏi có tháng mà code dùng `P.day`, `P.quarter`… thay cho `P.month`. Trên bank train không bắt nhầm câu nào.
- `doubt()` hỏi lại khi kết quả bằng 0 (chỉ 4/3000 đáp án train bằng 0) hoặc khi tỉ trọng âm; vẫn vậy thì bỏ trống.
- `scripts/redo.py` chỉ chạy lại các câu bị bắt (25 câu), rồi ghép vào v3.
- File nộp được ghi lại nguyên vẹn khi chạy xong: lượt v3 bị ghi đè giữa chừng, mất 6 dòng.

Chưa sửa: "dòng máy" ở dev_014 vẫn xếp lẫn phụ kiện.

### 13/11 trưa: lớp kiểm chứng tổng quát (thay `doubt()` và luật tháng tạm)
- `src/asked.py`: đọc kỳ trong câu hỏi bằng luật: tháng (kể cả `T7/2025`, `5/26`), quý, N ngày gần nhất, hôm qua/nay, tuần này/trước, tháng này/trước, năm.
  - `Recorder` ghi lại các kỳ chương trình tạo bằng P; kỳ hỏi phải nằm trong số đó, so sau khi cắt tới hôm nay.
  - Train: đọc được kỳ của 2458/2928 chương trình trong bank, **0 lần bắt nhầm**. Log v3: bắt 18 câu, đều sai thật.
- `src/verify.py`, cứng (thử lại một lần, vẫn sai thì bỏ trống):
  - kết quả 0 (trừ câu hỏi hàng ế và % thay đổi);
  - tỉ trọng âm hoặc trên 100%;
  - tên trả về không có trong dữ liệu;
  - danh sách ngắn hơn số câu hỏi đòi;
  - ngày trả về ngoài kỳ hỏi.
  - Đo trên 2930 đáp án train: **0 lần bắt nhầm** (`python -X utf8 -m training_code.verify_fp`).
- `src/verify.py`, mềm: câu hỏi nhắc một giá trị của dữ liệu (hàng, nhóm, khách, thợ, kênh…) mà chương trình không dùng.
  - Model gửi lại đúng chương trình cũ thì giữ đáp án.
  - Train: bắt nhầm 1/2928.
- Kết quả 0 thì lời nhắc thử lại kèm các giá trị hay gặp của cột chữ mà chương trình lọc.
- `scripts/redo.py pick LOG_DIR DATA OUT`: dựng lại shop theo manifest đã log, chạy lại chương trình đã log, rồi áp đúng các kiểm tra trên. Log v3: 36 câu cần chạy lại.
- Oracle train vẫn 0,9757. Prompt lần đầu không đổi, nên chạy lại riêng các câu bị bắt cho kết quả bằng chạy lại cả bài.
- Còn lại: khoảng 97 câu public chạm luật ngành (điện thoại 45, sửa xe 32, mẹ & bé 20). v3 bỏ trống 28 câu, viết pandas 22 câu, 47 câu gọi hàm chung nên dễ sai định nghĩa → hướng tiếp: hàm cho từng luật ngành trong rulebook.

### 13/11 chiều: sổ phụ dùng được cho mọi ngành (không viết hàm theo ngành)
Không viết hàm riêng cho luật điện thoại, sửa xe hay mẹ & bé: private có ngành lạ, và hàm theo ngành sẽ làm điểm public cao hơn thực lực. Thay vào đó:
- `src/ledger.py`: `entries()` điền ô chữ và ngày của dòng tiếp (một lần trả, một kỳ góp) từ dòng đầu mục, đánh số `_entry`; không bao giờ chép số tiền, nên tổng theo cột giữ nguyên.
  - Dòng tiếp: ngày đầu mục trống, có ô khác, và quá nửa ô chữ trống (danh sách khách thiếu ngày không bị điền).
  - Trên 44 shop chỉ dev_014 (sổ nợ, sổ góp) được điền.
- `links()` tìm cột của sổ phụ trỏ về mã đơn bán (`26`, `26.0`, `STT 26`), hoặc về giá trị của sổ bán (IMEI, mã hàng, mã khách). Kết quả ghi ở mục `### Links` của thẻ shop.
- `m.with_order(df, col)`: thêm order_id, o_date, o_status, `counted` (đơn không huỷ, không trả) vào bất kỳ bảng nào.
- T trong chương trình là các bảng đã điền (`shop.views`). SYSTEM có thêm một mục cách dùng sổ phụ (đếm theo `_entry`, nối đơn bằng `with_order`).
- Prompt đổi cho mọi câu → public phải chạy lại cả 1000 câu, Drive `zaloai/public_run_v4`. `redo.py` không còn tương đương.
- Oracle train vẫn 0,9757; 88 test qua.

### 13/11 chiều: public v4 (chạy đủ, có lớp kiểm chứng + sổ phụ) và đọc log
- v4 so với v3: 897 câu giống, 103 câu khác.
  - 32/36 câu lớp kiểm chứng bắt trên v3 đã có đáp án mới, phần lớn hợp lý (đọc đúng `T7/2025`, `5/26`…).
  - Câu ra 0 chuyển thành bỏ trống.
- Lỗi chung tìm trong log v4, đã sửa:
  - `type: unanswerable` bị bộ đọc từ chối (chỉ nhận 7 kiểu) → khoảng 15 câu về giới tính, địa chỉ, lý do bị bỏ trống. Giờ nhận; `from_thinking` cũng nhận khối `no_data`.
  - Hàm top-k với k=1 trả list cho câu hỏi kiểu string → bỏ trống. Giờ list một phần tử thành chuỗi.
  - Đáp án tháng ghi `string` ("tháng 7/25", repr của tuple ngày) → `as_day` đổi về `date` `YYYY-MM`. Train: mọi câu "tháng nào" có đáp án kiểu `date`.
  - "số" bỏ dấu thành "so", khớp nhầm luật "so sánh" → `CHANGE` chỉ nhận "so với / so cùng kỳ / so tháng…".
  - Kiểm tra mềm theo nhóm hàng hay kênh làm model lọc sai ("tiền thu máy cũ" → nhóm "máy cũ") → chỉ còn tên hàng, khách, thợ, nhà cung cấp.
  - "Hết/đến hôm nay" mà chương trình chỉ dùng `P.today()` → lỗi cứng (`TO_TODAY`).
- Train: oracle 0,9757; đáp án train bị bắt nhầm 0/2930; 91 test.
- `runs/submission_v4c.jsonl` (gitignored) = v4 cộng các sửa chỉ cần log, không gọi lại model: 4 tháng → `date`, 3 list một phần tử, 6 `unanswerable` lấy từ phần suy nghĩ, 1 câu bỏ trống.
