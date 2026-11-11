"""Self-check for score.py: python scripts/test_score.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from score import score_one as f

g = {"type": "string", "answer": "Xi măng Hà Tiên PCB40 bao 50kg", "alias": ["SP0001"]}
assert f({"type": "string", "answer": "xi măng hà tiên pcb40, bao 50kg"}, g) == 1
assert f({"type": "string", "answer": "sp0001"}, g) == 1
assert f({"type": "string", "answer": "Xi mang Ha Tien PCB40 bao 50kg"}, g) == -1  # diacritics matter
assert f({"type": "abstain"}, g) == 0 and f(None, g) == 0

t = {"type": "table", "answer": {"columns": ["khach_hang", "so_tien_no"], "rows": [["An", 5], ["Bich", 3]]},
     "alias": [["KH1"], ["KH2"]]}
assert f({"type": "table", "answer": {"columns": ["so_tien_no", "khach_hang"], "rows": [[3, "kh2"], [5, "An"]]}}, t) == 1
assert f({"type": "table", "answer": {"columns": ["a", "b"], "rows": [["An", 5], ["Bich", 4]]}}, t) == -1

assert f({"type": "percent", "answer": 2.084}, {"type": "percent", "answer": 2.08}) == 1
assert f({"type": "number", "answer": "5"}, {"type": "number", "answer": 5}) == -1
assert f({"type": "number", "answer": True}, {"type": "number", "answer": 1}) == -1
assert f({"type": "list", "answer": ["sp0003"]}, {"type": "list", "answer": ["Cà phê sữa (M)"], "alias": [["SP0003"]]}) == 1
assert f({"type": "number", "answer": 1}, {"type": "unanswerable", "answer": None}) == -1
print("score.py selftest ok")
