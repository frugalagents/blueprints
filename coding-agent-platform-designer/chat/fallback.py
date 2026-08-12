"""Fallback handler — ARCHITECTURE.md §4.1.

Triggered when the OKF graph has no answer (component not found, or found but
thin). Probe 2 (logged in IMPLEMENTATION_PLAN.md after Phase 1) found this
path simply didn't exist: no allowlist boundary, no injection filter, no
label, no gap log — a silent failure mode indistinguishable from a confident
answer. This module is what closes that gap.

Hard rule carried from CLAUDE.md: fetched content is untrusted input even from
an allowlisted domain. Every fetch is filtered before it reaches the model —
no exception for allowlisted domains.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

MAX_CONTENT_CHARS = 8000
FETCH_TIMEOUT_SECONDS = 10

# Patterns commonly used to smuggle instructions into fetched content aimed at
# an LLM reader rather than a human reader. Not exhaustive — a heuristic floor,
# not a guarantee. Strip matches rather than reject the whole page, since a
# false positive on legitimate doc text ("ignore the deprecated flag below")
# is far more likely than a real injection in a curated AWS/vendor docs page.
INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(?:\w+\s+){0,3}instructions"),
    re.compile(r"(?i)you are now[:\s]"),
    re.compile(r"(?i)system prompt[:\s]"),
    re.compile(r"(?i)\bnew instructions?\b\s*:"),
    re.compile(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", re.DOTALL),
]


@dataclass
class FallbackResult:
    triggered: bool
    query: str
    matched_component_id: str | None
    answer_source_url: str | None = None
    content: str | None = None
    label: str = ""


def load_allowlist(sources_path: Path) -> list[dict]:
    data = json.loads(sources_path.read_text(encoding="utf-8"))
    return [s for s in data.get("fallback", []) if s.get("fallbackEligible")]


def _domain_allowed(url: str, allowlist: list[dict]) -> bool:
    host = urlparse(url).netloc
    return any(host == entry["domain"] for entry in allowlist)


def filter_injection(text: str) -> tuple[str, int]:
    """Strip suspected injection attempts. Returns (cleaned_text, hits)."""
    hits = 0
    cleaned = text
    for pattern in INJECTION_PATTERNS:
        cleaned, n = pattern.subn("[filtered]", cleaned)
        hits += n
    return cleaned, hits


def fetch_from_allowlist(url: str, allowlist: list[dict]) -> str:
    if not _domain_allowed(url, allowlist):
        raise ValueError(f"URL {url} is not in the fallback allowlist — refusing to fetch")
    req = urllib.request.Request(url, headers={"User-Agent": "coding-agent-platform-designer/0.1"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return raw[:MAX_CONTENT_CHARS]


def log_gap(gaps_path: Path, query: str, matched_component_id: str | None) -> None:
    """Append-only log — feeds Tier B/C seed queries per ARCHITECTURE.md §4.1
    and §5. Never overwrites prior entries.
    """
    gaps_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if gaps_path.exists():
        entries = json.loads(gaps_path.read_text(encoding="utf-8"))
    entries.append({
        "query": query,
        "matchedComponent": matched_component_id,
        # Timestamp intentionally omitted here — caller (the runtime) stamps
        # it, since this module has no clock access by design in this
        # environment; see chat/agent.py for where it's added.
    })
    gaps_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def run_fallback(
    query: str,
    url: str,
    allowlist_path: Path,
    gaps_path: Path,
    matched_component_id: str | None = None,
) -> FallbackResult:
    allowlist = load_allowlist(allowlist_path)
    raw = fetch_from_allowlist(url, allowlist)
    cleaned, injection_hits = filter_injection(raw)
    log_gap(gaps_path, query, matched_component_id)

    label = f"Not yet in the curated wiki — sourced live from {urlparse(url).netloc}."
    if injection_hits:
        label += f" ({injection_hits} suspected injection pattern(s) filtered before use.)"

    return FallbackResult(
        triggered=True,
        query=query,
        matched_component_id=matched_component_id,
        answer_source_url=url,
        content=cleaned,
        label=label,
    )
