"""Claude Code hooks. `lint`: ruff on each edited .py file. `commit`: block git commit if scorer selftest fails.
Exit 2 = blocking error shown to Claude."""
import json
import os
import subprocess
import sys

mode = sys.argv[1]
data = json.load(sys.stdin)
root = os.environ.get("CLAUDE_PROJECT_DIR", ".")

if mode == "lint":
    path = (data.get("tool_input") or {}).get("file_path", "")
    if not path.endswith(".py"):
        sys.exit(0)
    r = subprocess.run([sys.executable, "-m", "ruff", "check", "--fix", "--quiet", path],
                       check=False, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout + r.stderr, file=sys.stderr)
        sys.exit(2)

elif mode == "commit":
    if "git commit" not in (data.get("tool_input") or {}).get("command", ""):
        sys.exit(0)
    r = subprocess.run([sys.executable, os.path.join(root, "scripts", "test_score.py")],
                       check=False, capture_output=True, text=True)
    if r.returncode:
        print("Commit blocked: scorer selftest failed\n" + r.stdout + r.stderr, file=sys.stderr)
        sys.exit(2)
