---
description: Chấm điểm một file submission trên tập train (PA theo luật thi) và phân tích lỗi
argument-hint: <pred.jsonl> [--questions subset.jsonl]
---
Chạy: `python scripts/score.py $ARGUMENTS --show 30`

Nếu không có tham số, dùng file `submission.jsonl` mới nhất trong `outputs/`.

Sau đó:
1. Báo PA tổng và PA theo từng type.
2. Với các câu sai, tra câu hỏi trong `data/train/train/questions.jsonl` theo id, gom lỗi theo nhóm nguyên nhân (sai luật rulebook, sai kỳ thời gian, sai format, nên trả `unanswerable`, nên `abstain`...).
3. Đề xuất 1–3 sửa có lợi nhất. Nhớ: sai bị −1, bỏ trống 0, nên câu không chắc thì abstain.
