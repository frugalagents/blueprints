"""In-process decision log for AgentCore Policy (CAP-H) decisions.

The ``/api/chat`` relay appends every REAL ``policy`` SSE event it passes through
to this bounded ring buffer; ``GET /api/policy`` reads it back as the recent
decision log. This keeps the log genuine (actual stream decisions from the
enforced Gateway) and truthfully labeled as recent-turn decisions — nothing is
fabricated. The buffer is process-local and resets on BFF restart, which is the
honest behavior for a POC (it is not a durable audit store).
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List

# Keep the most recent decisions; bounded so the process never grows unbounded.
_MAXLEN = 50
_decisions: Deque[Dict[str, str]] = deque(maxlen=_MAXLEN)
_lock = threading.Lock()


def record(decision: str, principal: str, resource: str) -> None:
    """Append one real policy decision (ALLOWED/DENIED) with a UTC timestamp."""
    entry = {
        "timestamp": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "decision": decision,
        "principal": principal,
        "resource": resource,
    }
    with _lock:
        _decisions.append(entry)


def recent(limit: int = _MAXLEN) -> List[Dict[str, str]]:
    """Return recent decisions, newest first (a shallow copy, safe to serialize)."""
    with _lock:
        items = list(_decisions)
    items.reverse()
    return items[:limit]
