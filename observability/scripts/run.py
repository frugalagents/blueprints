"""Start all three agents, Collector and UI using the installed isolated runtimes."""
import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open-browser', action='store_true', help='Open Connected when the app is ready.')
    args = parser.parse_args()
    app_port = int(os.environ.get("PIPELINE_APP_PORT", "8775"))
    required = (".runtime/checkpoint1/.venv/bin/python",
                ".runtime/agent2/.venv/bin/python",
                ".runtime/agent3-langchain/.venv/bin/python",
                ".runtime/collector/otelcol-contrib")
    missing = [name for name in required if not (ROOT / name).exists()]
    if missing:
        raise SystemExit("Run setup first. Missing: " + ", ".join(missing))
    for port in (app_port, 4318, 13133, 8786):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is busy. Stop the other process first.")
    print(f"Open http://127.0.0.1:{app_port}/connected.html and click Start workflow.",
          flush=True)
    print("Bedrock uses your own SDK credentials. Press Ctrl+C to stop.", flush=True)
    process = subprocess.Popen(
        [str(ROOT / required[0]), str(ROOT / "app/agent3-langchain/start_demo.py")],
        cwd=ROOT, env=os.environ.copy())
    def open_when_ready():
        for _ in range(120):
            if process.poll() is not None:
                return
            try:
                with urlopen(f'http://127.0.0.1:{app_port}/api/pipeline/status', timeout=.5) as response:
                    import json
                    if json.load(response).get('collector_healthy'):
                        webbrowser.open(f'http://127.0.0.1:{app_port}/connected.html')
                        return
            except Exception:
                pass
            time.sleep(.5)
    if args.open_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        raise SystemExit(process.wait())
    except KeyboardInterrupt:
        # The launcher receives terminal SIGINT and shuts down its children.
        try:
            process.wait(timeout=35)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
