"""Write training_code/data_manifest.json (Docker guideline 8): every data and model file with its size and SHA-256.

    python -X utf8 -m training_code.manifest          # run from the code root (/code in the image)

Run at image build time, so the checksums are those of the files inside the image.
"""

import hashlib
import json
import os

MODEL = {"name": "Qwen/Qwen3.5-9B", "url": "https://huggingface.co/Qwen/Qwen3.5-9B",
         "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a", "license": "apache-2.0",
         "released": "2026-02-27", "parameters": 9_653_100_000}  # fmt: skip
GROUPS = {
    "provided_train_data": "data/train/train",  # the organizers' training split, as released
    "example_bank": "training_code/data",  # generated from it by training_code/bank.py
    "splits": "training_code/splits",  # dev / holdout / hidden-rule question subsets of train
    "dev_manifests": "training_code/manifests",  # hand-written schema patches of 4 train shops, development only
    "model": "models/model",  # inference weights, Qwen3.5-9B at MODEL["revision"]
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def lines(path):
    with open(path, encoding="utf-8") as f:
        return sum(1 for x in f if x.strip())


def main():
    out = {"base_model": MODEL, "training": "none: no model was trained or fine-tuned", "groups": {}}
    for group, root in GROUPS.items():
        files = []
        for d, dirs, names in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x != ".cache")
            for n in sorted(names):
                p = os.path.join(d, n).replace(os.sep, "/")
                row = {"path": p, "bytes": os.path.getsize(p), "sha256": sha256(p)}
                if p.endswith(".jsonl"):
                    row["records"] = lines(p)
                files.append(row)
        out["groups"][group] = {"root": root, "files": len(files), "bytes": sum(f["bytes"] for f in files), "items": files}
    train = GROUPS["provided_train_data"]
    if os.path.exists(f"{train}/questions.jsonl"):
        out["counts"] = {"train_questions": lines(f"{train}/questions.jsonl"), "train_shops": len(os.listdir(f"{train}/data")),
                         "example_bank": lines("training_code/data/bank.jsonl")}  # fmt: skip
        out["counts"].update({f"split_{n[:-6]}": lines(f"training_code/splits/{n}") for n in sorted(os.listdir("training_code/splits")) if n.endswith(".jsonl")})
    with open("training_code/data_manifest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print({g: (v["files"], v["bytes"]) for g, v in out["groups"].items()}, out.get("counts"))


if __name__ == "__main__":
    main()
