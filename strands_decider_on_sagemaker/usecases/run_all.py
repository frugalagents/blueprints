"""Run every use-case demo against the live endpoint and print one summary line each."""
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
rows = []
for demo in sorted(root.glob("[0-9][0-9]_*/demo.py")):
    p = subprocess.run([sys.executable, str(demo)], capture_output=True, text=True)
    m = re.search(r"SUMMARY (.+?) (\d+)/(\d+)", p.stdout)
    if p.returncode or not m:
        rows.append((demo.parent.name, "ERROR", p.stderr.strip().splitlines()[-1] if p.stderr else "no summary"))
    else:
        rows.append((demo.parent.name, f"{m.group(2)}/{m.group(3)}", f"{100 * int(m.group(2)) // int(m.group(3))}%"))
for name, a, b in rows:
    print(f"{name:<28} {a:>8}  {b}")
