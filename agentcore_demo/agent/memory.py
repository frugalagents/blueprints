"""In-process session-memory shim for the Advisor Copilot POC (task 2.4).

A thread-safe, in-memory conversation store keyed by ``session_id``. The public
interface mirrors what the future AgentCore Memory integration (Phase 4/5) will
expose, so the agent code can be re-pointed at the managed service by swapping
the singleton without touching call sites:

    append_turn(session_id, role, content)
    get_history(session_id) -> list[{"role", "content"}]
    list_sessions() -> list[str]
    clear(session_id)

Each turn is a dict ``{"role": "user"|"assistant", "content": str}`` so the
history slots directly into a prompt/transcript. No persistence, no AWS.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Dict, List


class SessionMemory:
    """Thread-safe in-process store of per-session conversation turns."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, List[Dict[str, str]]] = {}

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        """Append a single turn to a session's history (creating it if new)."""
        with self._lock:
            self._sessions.setdefault(session_id, []).append(
                {"role": role, "content": content}
            )

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Return a copy of the session's turns (empty list for unknown ids)."""
        with self._lock:
            return [dict(turn) for turn in self._sessions.get(session_id, [])]

    def list_sessions(self) -> List[str]:
        """Return the ids of all known sessions."""
        with self._lock:
            return list(self._sessions.keys())

    def clear(self, session_id: str) -> None:
        """Drop all stored turns for a session (no-op if unknown)."""
        with self._lock:
            self._sessions.pop(session_id, None)


class AgentCoreMemory:
    """AgentCore Memory-backed store with the same interface as ``SessionMemory``.

    Turns are persisted/read via the DATA-PLANE ``bedrock-agentcore`` client
    (``create_event`` / ``list_events``), per the Phase 4 contract:

        memoryId  = the Memory resource id (from infra/outputs.json / .env)
        sessionId = the shim's session_id (the chat session key)
        actorId   = the advisor id (so turns are scoped per advisor)

    Conversation turns are stored as ``conversational`` payloads with role
    USER / ASSISTANT. ``get_history`` returns the same
    ``[{"role", "content"}, ...]`` (oldest first) shape the local shim returns,
    so ``agent/agent.py`` call sites are untouched. ``list_sessions`` enumerates
    distinct sessions for the actor.

    This never provisions the Memory resource (that is ``infra/04_memory.py``);
    it only reads/writes events against the existing resource.
    """

    # Default advisor/actor id for the POC single-advisor demo. The Phase 4
    # contract uses the advisor id as the Memory actorId.
    DEFAULT_ACTOR_ID = "FA-77"

    _ROLE_TO_API = {"user": "USER", "assistant": "ASSISTANT"}
    _ROLE_FROM_API = {"USER": "user", "ASSISTANT": "assistant"}

    def __init__(self, memory_id: str, region: str,
                 actor_id: str | None = None) -> None:
        import boto3  # lazy: importing this module stays network-free

        self._memory_id = memory_id
        self._actor_id = actor_id or os.environ.get(
            "ADVISOR_ACTOR_ID", self.DEFAULT_ACTOR_ID
        )
        self._client = boto3.client("bedrock-agentcore", region_name=region)

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        """Persist one conversational turn as a Memory event."""
        api_role = self._ROLE_TO_API.get(role)
        # Only USER/ASSISTANT turns map cleanly; skip anything else (and empties).
        if not api_role or not content:
            return
        self._client.create_event(
            memoryId=self._memory_id,
            actorId=self._actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {"conversational": {"role": api_role, "content": {"text": content}}}
            ],
        )

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Return the session's turns (oldest first) as {role, content} dicts."""
        turns: List[Dict[str, str]] = []
        next_token = None
        while True:
            kwargs = {
                "memoryId": self._memory_id,
                "actorId": self._actor_id,
                "sessionId": session_id,
                "includePayloads": True,
                "maxResults": 100,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = self._client.list_events(**kwargs)
            for event in resp.get("events", []):
                for block in event.get("payload", []) or []:
                    conv = block.get("conversational")
                    if not conv:
                        continue
                    role = self._ROLE_FROM_API.get(conv.get("role", ""))
                    text = (conv.get("content") or {}).get("text", "")
                    if role and text:
                        turns.append({"role": role, "content": text})
            next_token = resp.get("nextToken")
            if not next_token:
                break
        # ListEvents returns most-recent first; reverse to oldest-first so the
        # history seeds the conversation in chronological order.
        turns.reverse()
        return turns

    def list_sessions(self) -> List[str]:
        """Return distinct session ids for the actor."""
        sessions: List[str] = []
        next_token = None
        while True:
            kwargs = {
                "memoryId": self._memory_id,
                "actorId": self._actor_id,
                "maxResults": 100,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = self._client.list_sessions(**kwargs)
            for sess in resp.get("sessionSummaries", resp.get("sessions", [])):
                sid = sess.get("sessionId") if isinstance(sess, dict) else sess
                if sid:
                    sessions.append(sid)
            next_token = resp.get("nextToken")
            if not next_token:
                break
        return sessions

    def clear(self, session_id: str) -> None:
        """No-op: event history expires via the Memory resource's TTL.

        The data plane does not expose a bulk per-session purge for the POC; the
        30-day event-expiry on the resource handles cleanup. Kept for interface
        parity with ``SessionMemory``.
        """
        return None


def _build_sessions():
    """Return the active session store: AgentCore Memory when MEMORY_ID is set,
    else the in-process shim (local dev / fallback).

    Resolution is defensive: if the Memory-backed store cannot be constructed
    (missing creds, bad region), fall back to the in-process shim so the agent
    still runs rather than crashing on import.
    """
    memory_id = os.environ.get("MEMORY_ID", "").strip()
    if not memory_id:
        return SessionMemory()
    region = os.environ.get("AWS_REGION", "us-east-1").strip() or "us-east-1"
    try:
        return AgentCoreMemory(memory_id=memory_id, region=region)
    except Exception:  # noqa: BLE001 - never let memory wiring break startup
        return SessionMemory()


# Module-level singleton the agent uses. When MEMORY_ID is configured this is a
# managed AgentCore Memory-backed store; otherwise the in-process shim. Both
# expose append_turn / get_history / list_sessions / clear, so agent call sites
# and the BFF are untouched by the swap.
SESSIONS = _build_sessions()
