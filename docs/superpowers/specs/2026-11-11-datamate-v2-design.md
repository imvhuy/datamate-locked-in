# DataMate v2: thiết kế lại (11/11/2026)

Trạng thái: **chờ duyệt**. Chưa có dòng code nào của bản này. Khi duyệt, tài liệu này thay `PLAN.md` §2–§6; §1 và §7 của `PLAN.md` giữ làm lịch sử.

## 1. Vì sao thiết kế lại

Bản hiện tại đạt 0,957 trên 300 câu train giữ riêng nhưng 0,548 trên public (khoảng 714 đúng, 166 sai, 120 bỏ trống). Khoảng cách đó là thứ private sẽ chấm: `competition/data.md` ghi private có loại hình kinh doanh không có ở cả train lẫn public, và mỗi split có rulebook riêng.

Bốn nguyên nhân gốc, đều kiểm được trong code:

1. **Tri thức đóng băng theo rulebook train.** `src/metrics.py` là 52 helper viết theo rulebook train. Rulebook public bỏ 3 luật (ứng với `avg_purchase_price`, `stock_split`, `price_on`: 139 câu train), sửa 3 luật, thêm khoảng 25 luật và 3 ngành. Code không so rulebook lúc chạy, nên helper áp luật cũ mà không báo.
2. **Một chương trình, không có độ tin cậy.** Mỗi câu chỉ sinh một chương trình; chạy ra số là nộp. Sai bị −1 nên 166 câu sai đắt hơn 120 câu bỏ trống.
3. **Sai lặng lẽ ở tầng dữ liệu.** `m.revenue(p, cust="tên không tồn tại")` trả 0 thay vì báo lỗi (đã thử trên `biz_105`); xếp hạng không phát hiện đồng hạng; shop do model ánh xạ schema không được kiểm bằng phép tính nào.
4. **Vận hành mỏng.** `exec` không giới hạn thời gian; không có đồng hồ tổng; `src/load.py` không bắt lỗi theo file và chỉ đọc CSV bằng `utf-8-sig`, nên một file hỏng làm sập cả lượt chạy.

Thêm một nguyên nhân về cách làm: thước đo phát triển là train (dễ, cùng mẫu) và leaderboard public. `PLAN.md` §3 định dùng "mỗi lượt nộp một thí nghiệm": nộp hai bản chỉ khác nhau ở một nhóm câu sẽ lộ nhóm đó đúng hay sai, tức là thu thập đáp án test mà Rule 6 cấm.

## 2. Mục tiêu

- **Mục tiêu:** PA cao nhất trên private do ban tổ chức chạy lại trong Docker.
- **Nguyên tắc 1:** cái lạ phải rơi về 0 điểm (bỏ trống), không rơi về −1.
- **Nguyên tắc 2:** mọi quyết định về độ chính xác dựa trên số đo từ train. Public chỉ nộp bản chạy toàn bộ ở mốc lớn và chỉ đọc điểm tổng.
- **Nguyên tắc 3:** không lượt chạy nào mất điểm vì lỗi vận hành.

Không làm trong bản này (kèm lý do):
- Fine-tune / LoRA: còn 5 ngày, máy dev không có GPU, lợi ích trên ngành mới chưa có bằng chứng.
- Model thứ hai (embedding, reranker): truy hồi BM25 đủ dùng và không tốn tham số.
- Helper riêng cho ngành của public (sửa xe, điện thoại, mẹ và bé): private lại có ngành khác.
- Biên dịch luật mới thành helper lúc khởi động: một helper sinh sai kéo sai cả nhóm câu.

Điều sẽ coi là thất bại dù điểm train cao: có nhánh code theo tên file, tên shop hay mã câu; lời giải đọc rulebook train thay cho rulebook lúc chạy; kết quả hai lần chạy lệch quá 0,03 PA.

## 3. Kiến trúc

```
/data ─► [1] Hiểu shop ──► bảng chuẩn + bảng gốc T + cờ nghi ngờ
rulebook ─► [2] Sổ luật ─► từng dòng luật: giữ nguyên / mở rộng / đổi / mới / đã bỏ
câu hỏi ─► [3] Hai lời giải độc lập ─► [4] Chạy cách ly ─► [5] Kiểm tra xác định ─► [6] Phán quyết ─► nộp + log
```

Giữ nguyên từ bản cũ: `src/load.py`, `src/parse.py`, `src/schema.py`, `src/mapper.py`, `src/shop.py`, `src/period.py`, phần lớn `src/metrics.py`, `training_code/bank.py`, `scripts/score.py`. Đổi nhiều nhất: `src/agent.py`. Mới: `src/rules.py`, `src/sandbox.py`, `training_code/sim.py`.

### [1] Hiểu shop (mỗi shop một lần)

Đường đi cũ giữ nguyên: loader → manifest bằng luật và lexicon → model lấp chỗ trống → bảng chuẩn. Bảng chuẩn vẫn mang cả cột gốc chưa ánh xạ (đã kiểm ở `_canon`), nên cột riêng của ngành mới vẫn với tới được từ `m.orders(p)`.

Thêm **phép kiểm bất biến** sau khi dựng bảng, không dùng model:
- số lượng × đơn giá ≈ thành tiền trên ít nhất 95% dòng (khi shop có cả ba cột);
- tổng thành tiền các dòng ≈ tiền hàng của đơn (khi có cả hai);
- cột ngày phủ 12–30 tháng (`data.md` cam kết khoảng này);
- tỉ lệ giá trị trạng thái không nhận ra dưới 2%.

Bất biến hỏng thì trường liên quan vào `shop.doubts`. Câu nào chạm trường bị nghi thì không được trả lời bằng một lời giải đơn lẻ.

Loader thêm: bắt lỗi theo từng file (file hỏng thành ghi chú, shop vẫn mở), thử thêm `utf-16` và `cp1258` cho CSV, tự dò dấu phân cách.

### [2] Sổ luật (mỗi split một lần)

`src/rules.py` tách `rulebook.md` thành các dòng luật `{mục, thuật ngữ, nội dung}` và so từng dòng với rulebook train đóng kèm image (chỉ dùng để so, không dùng để áp).

| Trạng thái | Nhận biết | Hệ quả |
|---|---|---|
| giữ nguyên | nội dung trùng sau chuẩn hoá | helper gắn với luật đó được dùng bình thường |
| mở rộng | chỉ thêm chữ, không bớt | helper vẫn hiện, phần chữ thêm được tô trong prompt; kết quả phải trùng với lời giải thứ hai |
| đổi | có chữ bị bớt hoặc thay | helper bị ẩn khỏi prompt, ví dụ dùng helper đó bị loại khỏi truy hồi |
| mới | không có ở rulebook train | gắn nhãn MỚI trong prompt; model phải tự dựng từ chữ của luật |
| đã bỏ | có ở train, không có lúc chạy | helper vẫn hiện nhưng kết quả phải trùng với lời giải thứ hai |

Mỗi method công khai của `Metrics` và `Periods` khai các dòng luật nó thực hiện trong một bảng `RULES` đặt cạnh code. Có test bắt buộc: mọi method đều có trong bảng và mọi khoá đều tồn tại trong rulebook train.

Với mỗi câu hỏi, 6 dòng luật khớp nhất (BM25 trên thuật ngữ và nội dung) được nêu lại ngay trước câu hỏi, kèm trạng thái. Toàn văn rulebook vẫn nằm trong phần đầu prompt.

### [3] Hai lời giải độc lập

| | Lời giải A: tra helper | Lời giải B: tự dựng |
|---|---|---|
| Model thấy | danh mục helper còn hiệu lực + ví dụ một dòng từ bank | chỉ bảng chuẩn (`m.sold`, `m.orders`, `m.rows`, `T`), `P`, vài công cụ chung; không có danh mục helper |
| Ví dụ | bank hiện tại (2.928 câu) | bank ghép: chương trình pandas trên bảng chuẩn, chỉ giữ cái ra đúng đáp án train |
| Mạnh ở | luật quen, thuật toán khó (nợ, tồn, giá vốn) | luật mới, ngành mới, cột riêng của shop |

Căn cứ để tin B làm được: 85% chương trình trong bank (2.484 / 2.928) chỉ là lọc đơn hợp lệ theo kỳ rồi cộng, đếm, chia hoặc xếp hạng. Phần thuật toán khó chỉ chiếm 14,5%.

Bank ghép được dựng theo hai bước, đều thuộc `training_code/`: (a) viết tay bản mở rộng cho 10 helper phổ biến nhất, kiểm bằng đáp án train; (b) cho model chạy lời giải B trên train, giữ chương trình ra đúng đáp án, lưu nguyên prompt và phản hồi theo yêu cầu của hướng dẫn Docker.

Khi A bị ẩn (luật đổi hoặc mới) hoặc A hỏng, chạy thêm **B′**: cùng đề bài với B nhưng bộ ví dụ khác và tắt suy nghĩ. Hai phần rulebook và mô tả shop đặt đầu prompt để vLLM dùng lại bộ đệm tiền tố.

Trước khi model thấy câu hỏi: bộ giải telex xác định (`ddown` → `đơn`, `huyr` → `huỷ`) dùng cho truy hồi; câu gốc vẫn đưa nguyên văn cho model. Thước đo của nó là số câu train mà mẫu bắt được (hiện trượt 72 câu, trong đó khoảng 32 câu có dấu hiệu lỗi gõ telex theo một phép đếm thô).

### [4] Chạy cách ly

`src/sandbox.py`: mỗi shop một tiến trình con giữ shop đã nạp, nhận `(kiểu, code, as_of)` qua ống, trả kết quả. Tiến trình cha đặt hạn 20 giây mỗi chương trình; quá hạn thì diệt và dựng lại tiến trình con. Giới hạn bộ nhớ bằng `resource.setrlimit` trên Linux.

### [5] Kiểm tra xác định (sau khi chạy, trước khi tin)

- Giá trị lọc không khớp dòng nào **trong cả bảng** (tên khách, tên hàng, nhóm, nhân viên) → lỗi `UnknownEntity`, không trả 0.
- Xếp hạng có đồng hạng ở ranh giới top k, hoặc ở hạng 1 với câu "… nhất" → bỏ trống. `Evaluation.md` nói những câu như vậy đã bị loại khỏi đề, nên đồng hạng nghĩa là ta tính sai.
- Đáp án kiểu tên (string, phần tử list, cột đầu của table) phải có nguyên văn trong dữ liệu shop.
- Đúng format theo kiểu (dùng lại `scripts/check_submission.py`); NaN, vô cực, chia cho 0 → bỏ trống.
- Kỳ nằm hẳn ngoài khoảng ngày có dữ liệu → `unanswerable` (đã có).

### [6] Phán quyết

| Tình huống | Quyết định |
|---|---|
| A và B trùng nhau | trả lời |
| A có, B lệch hoặc hỏng; luật giữ nguyên, shop không bị nghi | trả lời A **chỉ khi** nhóm đó có độ chính xác trên train ≥ ngưỡng, ngược lại bỏ trống |
| A bị ẩn hoặc hỏng; B và B′ trùng nhau | trả lời B |
| còn lại | bỏ trống |
| `unanswerable` | khi phép kiểm xác định báo, hoặc cả hai lời giải cùng gọi `no_data()` và shop không bị nghi |

"Nhóm" = (helper đứng đầu của A × trạng thái luật × shop do luật hay do model ánh xạ). Bảng độ chính xác theo nhóm được đo trên train và các phép mô phỏng ở mục 4, đóng thành file JSON trong image. Ngưỡng khởi điểm 0,8; điểm hoà vốn của luật chấm là 0,5.

## 4. Cách đo (chỉ dùng train)

`training_code/sim.py` tạo một split tạm từ train rồi chạy đúng đường code sản phẩm:

| Phép mô phỏng | Cách làm | Mô phỏng điều gì ở private |
|---|---|---|
| Ẩn luật | xoá vài dòng khỏi rulebook tham chiếu → lúc chạy chúng thành "mới", helper bị ẩn | rulebook thêm luật |
| Ẩn ngành | bỏ shop của một ngành khỏi bank và lexicon, xoá mục ngành khỏi rulebook tham chiếu | loại hình kinh doanh mới |
| Đổi da | đổi tên cột, bỏ dấu, viết tắt, xoá mô tả cột trong README, đổi tên file | shop có schema lạ |
| Đổi luật | sửa chữ 3–4 luật có tham số (tuần bắt đầu Chủ nhật, giờ ca, N ngày gần nhất không gồm hôm nay), tính lại đáp án | rulebook sửa luật |

Mỗi phép báo ba số: PA, độ chính xác trên câu có trả lời, tỉ lệ trả lời. Mốc chấp nhận (đề xuất của tôi, chưa có số đo): độ chính xác trên câu có trả lời ≥ 0,85 ở mọi phép; PA trên `holdout300` không dưới 0,93.

`dev300` được đọc từng câu sai; `holdout300` chỉ báo điểm. Các phép mô phỏng chia shop theo cùng cách: sửa lỗi trên nửa đầu, báo điểm trên nửa sau.

Public: nộp tối đa hai lần, đều là bản chạy toàn bộ (sau khi các phép mô phỏng đạt mốc, và bản Docker cuối). Không phân tích đúng sai theo shop hay theo nhóm câu.

## 5. Vận hành

- **Đồng hồ tổng:** hạn 5 giờ 15 phút tính từ lúc `predict.sh` bắt đầu. Ngân sách mỗi câu = thời gian còn lại / số câu còn lại. Thiếu thời gian thì hạ dần: bỏ B′ → chỉ chạy A → bỏ trống phần còn lại.
- **Server model:** một server chết thì dồn sang server còn lại; mỗi lời gọi thử lại 2 lần; hết cách thì bỏ trống câu đó.
- **Prompt:** ước lượng số token; quá ngân sách thì cắt theo thứ tự ví dụ (8 → 4), mẫu giá trị trong thẻ shop, rồi các mục rulebook không khớp câu hỏi.
- **Tái lập:** `temperature 0`, `seed 0`, câu gắn server theo `crc32(id)`. Trước khi nộp chạy image hai lần và so `submission.jsonl`. Cơ chế "trùng mới trả lời" làm giảm số câu đổi đáp án do batching GPU.
- **Log:** mỗi câu một dòng có `id, answer, elapsed_ms, thinking` (danh sách theo thứ tự gọi), `code` và `output` của lời giải được chọn, thêm `candidates` chứa mọi lời giải đã chạy và lý do phán quyết.
- **Đóng gói:** sửa `.dockerignore` để tập train vào image dưới `training_code/data/`; thêm `training_code/README.md`, `requirements.txt`, `data_manifest.json`; `README.md` khai model và nói rõ không huấn luyện.

## 6. Thứ tự làm và điểm cắt

| Ngày | Việc | Cần GPU |
|---|---|---|
| 11/11 | `src/rules.py` + bảng `RULES` + test; ba phép kiểm ở [5]; loader bắt lỗi theo file | không |
| 12/11 | `src/sandbox.py`, đồng hồ tổng, cắt prompt; `sim.py` (ẩn luật, ẩn ngành); prompt B và bank ghép bước (a) | lượt đo 1 |
| 13/11 | sửa theo lỗi của mô phỏng; đổi da + bất biến; build Docker v0, gửi Test Solution | lượt đo 2 |
| 14/11 | đổi luật; chốt bảng phán quyết; chạy hai lần so kết quả; nộp public lần 1 | lượt đo 3 |
| 15/11 | đóng băng; tài liệu đóng gói; image cuối, md5, tải lên | build |
| 16/11 | dự phòng; nộp trước 18:00 | |

Cắt khi trễ, theo thứ tự: mô phỏng đổi luật → mô phỏng đổi da → B′ → bank ghép bước (b). Không cắt: sổ luật, chạy cách ly, đồng hồ tổng, ba phép kiểm, chạy thử Docker.

## 7. Việc người duyệt cần quyết

1. **Máy GPU.** Mỗi ngày 12–14/11 cần một lượt đo trên GPU (bản cũ dùng A100 Colab). Ngày 20–23/11 đội phải tự chạy image đã đóng băng trên private, cần máy có Docker và GPU.
2. **Model.** Mặc định giữ Qwen3.5-9B (model duy nhất đã có số đo). Thử thêm một model 14B chỉ khi còn lượt GPU, và chọn theo điểm mô phỏng chứ không theo `holdout300`.
3. **Tốc độ.** Hai đến ba lời giải mỗi câu làm lượt chạy chậm hơn 2–3 lần (ước lượng, chưa đo). Vẫn rất xa hạn 6 giờ nhưng thiệt ở tiêu chí hoà điểm.

## 8. Giới hạn đã biết

- Lỗi ánh xạ schema không làm hỏng bất biến nào thì cả hai lời giải cùng sai giống nhau; phép "trùng nhau" không bắt được.
- Lời giải B cho nợ, tồn kho, giá vốn sẽ thường hỏng; các nhóm đó dựa vào A và bảng phán quyết.
- Phép mô phỏng chỉ xáo trộn những gì train có. Một loại hình có cấu trúc dữ liệu chưa từng thấy (ví dụ đặt phòng theo đêm) vẫn có thể rơi vào bỏ trống hàng loạt; thiết kế này bảo đảm nó không rơi vào sai hàng loạt.
