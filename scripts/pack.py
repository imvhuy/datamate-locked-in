"""Zip what a GPU machine (Colab) needs. Leaves out caches and model weights.

    python scripts/pack.py           # -> datamate.zip: code + data + rulebooks (first upload, ~190 MB)
    python scripts/pack.py code      # -> datamate_code.zip: code only (updates, < 1 MB); unzip -o over the old copy
"""
import os
import sys
import zipfile

CODE = ["src", "training_code", "scripts", "predict.sh", "requirements.txt", "Dockerfile"]
SKIP = (".cache", "__pycache__", ".ruff_cache")
code_only = sys.argv[1:] == ["code"]
out = "datamate_code.zip" if code_only else "datamate.zip"

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for top in CODE + ([] if code_only else ["data"]):
        if os.path.isfile(top):
            z.write(top)
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if d not in SKIP]
            for f in files:
                z.write(os.path.join(root, f))
print(f"{out}: {os.path.getsize(out) / 1e6:.1f} MB")
