"""Shared data model: one Candidate becomes one AWS Agent Registry record."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-./]")


def safe_name(*parts: str) -> str:
    """Build a registry-valid record name (a-zA-Z0-9 _ - . /, max 255)."""
    name = "/".join(_NAME_RE.sub("-", p.strip("/")) for p in parts if p)
    if not name or not name[0].isalnum():
        name = "x" + name
    return name[:255]


@dataclass
class Candidate:
    name: str                      # unique key in the registry
    record_type: str               # MCP | AGENT | CUSTOM | SKILL | GATEWAY
    descriptors: dict              # typed descriptor structure for the API
    description: str = ""
    display_name: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def managed_prefix(self) -> str:
        return self.name.split("/", 1)[0]


# Prefixes of records owned by this scanner (never touches auto-detected ones).
MANAGED_PREFIXES = ("gw", "eks")
