"""Learn the column-name lexicon from the train shops whose README describes their columns.

For every described column we know its canonical field (src/schema.py DESC_RULES) and the role of its table;
the lexicon keeps those as {compact column name: {"role|field": count}} so a shop with a silent README can
still be mapped by column name. Train shops only: the public test is never used to build it.

    python -X utf8 -m training_code.lexicon      # writes src/lexicon.json
"""
import collections
import json
import os

from src.schema import LEXICON_PATH, compact, guess_table, read_readme
from src.shop import apply_patch
from training_code.bank import SPLIT, cached_tables


def main():
    lex = collections.defaultdict(collections.Counter)
    for biz in sorted(os.listdir(f"{SPLIT}/data")):
        shop_dir = f"{SPLIT}/data/{biz}"
        tables, readme = cached_tables(shop_dir), read_readme(shop_dir)
        manifest = {"tables": {n: guess_table(t, readme, lexicon={}) for n, t in tables.items()}}  # README only
        patch = f"training_code/manifests/{biz}.json"  # plus what we mapped by hand for silent train shops
        if os.path.exists(patch):
            apply_patch(manifest, patch)
        for spec in manifest["tables"].values():
            for col, field in spec["cols"].items():
                lex[compact(col)][f"{spec['role']}|{field}"] += 1
    with open(LEXICON_PATH, "w", encoding="utf-8") as f:
        json.dump({k: dict(v.most_common()) for k, v in sorted(lex.items())}, f, ensure_ascii=False, indent=0)
    print(f"{len(lex)} column names -> {LEXICON_PATH}")


if __name__ == "__main__":
    main()
