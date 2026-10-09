"""Install the local demo from a fresh checkout; no AWS credentials are copied."""
import argparse
import hashlib
import io
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_VERSION = "0.162.0"
BASE = ("https://github.com/open-telemetry/opentelemetry-collector-releases/"
        f"releases/download/v{COLLECTOR_VERSION}")
ENVS = (
    (".runtime/checkpoint1/.venv", "app/agent1/requirements.txt"),
    (".runtime/agent2/.venv", "app/agent2/requirements.lock.txt"),
    (".runtime/agent3-langchain/.venv", "app/agent3-langchain/requirements.lock.txt"),
)


def target():
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64",
            "x86_64": "amd64", "AMD64": "amd64"}.get(platform.machine())
    if not system or not arch:
        raise SystemExit("Supported: macOS/Linux ARM64 or x86_64. Use WSL2 on Windows.")
    return system, arch


def download(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def verified_binary(archive, checksums, filename):
    expected = None
    for line in checksums.decode().splitlines():
        pieces = line.split()
        if len(pieces) == 2 and pieces[1].lstrip("*") == filename:
            expected = pieces[0]
            break
    if expected is None or hashlib.sha256(archive).hexdigest() != expected:
        raise ValueError("Collector archive SHA256 verification failed.")
    # Read only the binary; never extract archive paths into the checkout.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        candidates = [m for m in tar.getmembers()
                      if Path(m.name).name == "otelcol-contrib" and m.isfile()]
        if len(candidates) != 1:
            raise ValueError("Collector archive must contain exactly one regular binary.")
        member = tar.extractfile(candidates[0])
        if member is None:
            raise ValueError("Collector binary could not be read.")
        return member.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Show steps without installing.")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 14):
        raise SystemExit("Use Python 3.14; it matches the demonstrated runtime.")
    system, arch = target()
    filename = f"otelcol-contrib_{COLLECTOR_VERSION}_{system}_{arch}.tar.gz"
    for directory, requirements in ENVS:
        print(f"Isolated environment: {directory} ← {requirements}", flush=True)
    print(f"Collector: {BASE}/{filename}", flush=True)
    if args.plan:
        return
    for directory, requirements in ENVS:
        env = ROOT / directory
        cfg = env / "pyvenv.cfg"
        if cfg.exists() and "include-system-site-packages = true" in cfg.read_text().lower():
            raise SystemExit(f"{env} uses global packages. Use a fresh checkout for setup.")
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(env)
        subprocess.run([str(env / "bin/python"), "-m", "pip", "install", "-r",
                        str(ROOT / requirements)], check=True)
        subprocess.run([str(env / "bin/python"), "-m", "pip", "check"], check=True)
    runtime = ROOT / ".runtime/collector"
    runtime.mkdir(parents=True, exist_ok=True)
    archive = download(f"{BASE}/{filename}")
    checksums = download(f"{BASE}/otelcol-contrib_{COLLECTOR_VERSION}_checksums.txt")
    data = verified_binary(archive, checksums, filename)
    with tempfile.NamedTemporaryFile(dir=runtime, delete=False) as file:
        file.write(data)
        staged = Path(file.name)
    staged.chmod(0o755)
    staged.replace(runtime / "otelcol-contrib")
    for folder in ("agent1", "agent2", "connected", "agent3", "agent3-langchain"):
        (ROOT / "data" / "captures" / folder).mkdir(parents=True, exist_ok=True)
    print("Setup complete. Next: python3.14 scripts/run.py", flush=True)


if __name__ == "__main__":
    main()
