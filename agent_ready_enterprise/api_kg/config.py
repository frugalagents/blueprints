from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "project_name": "enterprise-api-kg",
    "graph_file": "out/api_graph.json",
    "index_file": "out/vector_index",
    "api_base_url": "http://localhost:8080",
    "bedrock": {
        "region": "us-east-1",
        "planning_model": "global.anthropic.claude-sonnet-4-6",
        "inference_model": "global.anthropic.claude-sonnet-4-6",
        "synthesis_model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "embedding_model": "amazon.titan-embed-text-v2:0",
    },
    "retrieval": {
        "max_capabilities": 10,
        "graph_hops": 1,
    },
    "planning": {
        "max_steps": 12,
        "allow_mutations": False,
    },
    "demo_defaults": {
        "current_period": "2026-05",
        "previous_period": "2026-04",
        "aliases": {
            "sarah": {"employee_id": "EMP-1042"},
            "sarah chen": {"employee_id": "EMP-1042"},
        },
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    p = Path(path)
    if p.exists():
        with open(p) as f:
            loaded = yaml.safe_load(f) or {}
        config = deep_merge(config, loaded)
    return config
