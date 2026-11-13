"""The split's rulebook, rule by rule.

The description: "your agent must read and apply the rulebook of the split it is answering, not a copy of the
training rulebook". Every table row (and every paragraph or bullet outside a table) becomes one rule; the rules
nearest a question are quoted verbatim at the top of its prompt, so a definition the helpers were never written for
("thợ chăm chỉ nhất = nhiều ngày làm việc nhất") is in front of the model when it writes the program.
"""
import collections
import math
import re

from src.parse import fold


def parse(text):
    """Rulebook markdown -> [{"section", "key", "text"}] in reading order."""
    rules, section, sub = [], "", ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("### "):
            sub = s[4:].strip()
        elif s.startswith(("## ", "# ")):
            section, sub = s.lstrip("#").strip(), ""
        elif s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set("-: ") or cells[0] in ("Luật", "Cách nói", "Từ", "Cách ghi", "type"):
                continue  # separator or header row
            rules.append({"section": " / ".join(x for x in (section, sub) if x), "key": _plain(cells[0]),
                          "text": _plain(" | ".join(cells[1:]))})  # fmt: skip
        elif s and not s.startswith(("```", "![")):
            rules.append({"section": " / ".join(x for x in (section, sub) if x), "key": "", "text": _plain(s.lstrip("-• "))})
    return rules


def _plain(s):
    return re.sub(r"\*\*|`", "", s).strip()


def _words(s):
    return re.findall(r"[a-z0-9]+", fold(s))


class Rulebook:
    """BM25 over the rules: a rule's name and section weigh twice its body, so "chăm chỉ" finds its row."""

    def __init__(self, text):
        self.rules = parse(text)
        self.docs = [_words(f"{r['key']} {r['key']} {r['section']} {r['text']}") for r in self.rules]
        df = collections.Counter(w for d in self.docs for w in set(d))
        n = len(self.docs) or 1
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.avg = sum(map(len, self.docs)) / n

    def top(self, question, k=6):
        q = set(_words(question))
        scored = []
        for i, doc in enumerate(self.docs):
            tf = collections.Counter(doc)
            s = sum(self.idf[w] * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(doc) / self.avg)) for w in q if w in tf)
            if s > 0:
                scored.append((s, i))
        return [self.rules[i] for _, i in sorted(scored, key=lambda x: (-x[0], x[1]))[:k]]

    @staticmethod
    def show(rules):
        return "\n".join(f"- [{r['section']}] " + (f"{r['key']}: " if r["key"] else "") + r["text"] for r in rules)
