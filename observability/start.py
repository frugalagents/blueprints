"""One-command launcher for any macOS/Linux machine: python3 start.py

Finds Python 3.14, runs one-time setup if needed, then starts the demo.
With no arguments it opens Connected in the browser when ready; any arguments
you pass go to scripts/run.py instead.
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".runtime/checkpoint1/.venv/bin/python"
COLLECTOR = ROOT / ".runtime/collector/otelcol-contrib"


def find_python314():
    if sys.version_info[:2] == (3, 14):
        return sys.executable
    for name in ("python3.14", "python3"):
        path = shutil.which(name)
        if path and subprocess.run(
                [path, "-c", "import sys; raise SystemExit(sys.version_info[:2] != (3, 14))"]
        ).returncode == 0:
            return path
    return None


def main():
    if platform.system() not in ("Darwin", "Linux"):
        raise SystemExit("Supported: macOS or Linux. On Windows, run this inside WSL2.")
    if not (VENV_PYTHON.exists() and COLLECTOR.exists()):
        python = find_python314()
        if not python:
            raise SystemExit("Python 3.14 is required for one-time setup but was not found.\n"
                             "Install it (python.org, pyenv, or uv python install 3.14) and re-run.")
        print("First run: installing isolated environments and the Collector...", flush=True)
        subprocess.run([python, str(ROOT / "scripts/setup.py")], cwd=ROOT, check=True)
    args = sys.argv[1:] or ["--open-browser"]
    try:
        raise SystemExit(subprocess.run(
            [str(VENV_PYTHON), str(ROOT / "scripts/run.py"), *args],
            cwd=ROOT, env=os.environ.copy()).returncode)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
