from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import yaml


def load_openapi_file(path: Path) -> dict[str, Any]:
    with open(path) as f:
        if path.suffix.lower() == ".json":
            return json.load(f)
        return yaml.safe_load(f)


def iter_openapi_specs(specs_dir: str | Path) -> list[tuple[Path, dict[str, Any]]]:
    root = Path(specs_dir)
    files = sorted(
        list(root.rglob("*.yaml"))
        + list(root.rglob("*.yml"))
        + list(root.rglob("*.json"))
    )
    specs: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        specs.append((path, load_openapi_file(path)))
    return specs
