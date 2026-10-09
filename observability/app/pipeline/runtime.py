"""Shared runtime locations and loopback Collector health."""
import os
import secrets
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'app'
DATA = Path(os.environ.get('OBSERVABILITY_DATA_DIR', str(ROOT / 'data'))).resolve()
RUNTIME = ROOT / '.runtime/collector'
COLLECTOR_DATA = DATA / 'collector'

def capture_dir(name):
    path = DATA / 'captures' / name
    path.mkdir(parents=True, exist_ok=True)
    return path
TOKEN_FILE = COLLECTOR_DATA / 'ingest.token'


def ingest_token():
    COLLECTOR_DATA.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        fd=os.open(TOKEN_FILE, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd,'w') as file:file.write(secrets.token_hex(24))
    return TOKEN_FILE.read_text().strip()


def collector_health():
    try:
        with urlopen('http://127.0.0.1:13133', timeout=.3) as response:
            return response.status == 200
    except Exception:
        return False
