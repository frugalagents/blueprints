"""Chat-backend seam for the Advisor Copilot BFF (task 3.1).

The ``/api/chat`` route is backend-agnostic: it owns SSE serialization and
iterates whatever ``ChatBackend.stream`` yields. Phase 3 ships
``LocalChatBackend``, which calls the existing local agent ``run_agent``
in-process and re-yields its normalized event dicts
(``type`` in {token, tool_start, tool_stop, done, error}) unchanged.

This is the single Phase-5 swap point. When the AgentCore Runtime is deployed,
add a ``RuntimeChatBackend`` that POSTs to the Runtime's ``/invocations`` SSE
endpoint (using the pinned ``httpx`` dependency) and parses its SSE frames back
into the same event dicts, then flip ``get_chat_backend`` to return it. The
``/api/chat`` wire contract and all UI code stay unchanged.

Importing this module is side-effect-free: ``run_agent`` is imported lazily
inside ``LocalChatBackend.stream`` so that ``import bff.backends`` never touches
Bedrock or the network.
"""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict


class ChatBackend(ABC):
    """A source of normalized chat events for one agent turn.

    Implementations yield dicts whose ``type`` is one of
    ``token`` / ``tool_start`` / ``tool_stop`` / ``done`` / ``error`` — the same
    shape the local agent (``agent/agent.py``) and the Runtime ``/invocations``
    SSE server (``agent/app.py``) already produce.
    """

    @abstractmethod
    async def stream(self, session_id: str, message: str) -> AsyncIterator[Dict]:
        """Yield normalized event dicts for one turn of ``message``."""
        raise NotImplementedError


class LocalChatBackend(ChatBackend):
    """Local backend: runs the Strands agent in-process via ``run_agent``.

    Writes to the shared ``SESSIONS`` singleton (``run_agent`` persists the user
    and assistant turns), so the Memory tab shows the same sessions the chat
    uses.
    """

    async def stream(self, session_id: str, message: str) -> AsyncIterator[Dict]:
        # Lazy import: keeps ``import bff.backends`` free of Bedrock/network
        # side effects (``agent.agent`` builds Bedrock models on use, not import).
        from agent.agent import run_agent

        async for event in run_agent(message, session_id):
            yield event


class RuntimeChatBackend(ChatBackend):
    """Runtime backend: invokes the DEPLOYED AgentCore Runtime endpoint.

    Calls the data-plane ``bedrock-agentcore.invoke_agent_runtime`` with the
    runtime ARN and ``accept='text/event-stream'``, then parses the runtime's SSE
    frames — the identical ``event: <type>`` / ``data: <json>`` frames
    ``agent/app.py`` emits inside the container — back into the SAME normalized
    event dicts (``token`` / ``tool_start`` / ``tool_stop`` / ``done`` /
    ``error``) the ``/api/chat`` route serializes. The wire contract and the UI
    stay unchanged; only the source of the stream moves from in-process to the
    deployed Runtime.

    SigV4/IAM gates the (default-authorizer) runtime endpoint; boto3 signs the
    request from the BFF's ambient AWS credentials.
    """

    def __init__(self, runtime_arn: str, region: str) -> None:
        # The configured ARN may be the runtime ARN or the endpoint ARN
        # (.../runtime/<id>/runtime-endpoint/<name>). invoke_agent_runtime takes
        # the RUNTIME arn plus an optional endpoint qualifier, so split them.
        self._region = region
        marker = "/runtime-endpoint/"
        if marker in runtime_arn:
            base, qualifier = runtime_arn.split(marker, 1)
            self._runtime_arn = base
            self._qualifier = qualifier or "DEFAULT"
        else:
            self._runtime_arn = runtime_arn
            self._qualifier = None

    @staticmethod
    def _runtime_session_id(session_id: str) -> str:
        """runtimeSessionId must be >= 33 chars; pad the chat session id stably."""
        base = f"advisor-copilot-{session_id}"
        return base if len(base) >= 33 else (base + "-" + "0" * 33)[:40]

    def _invoke(self, session_id: str, message: str):
        """Blocking boto3 invoke; returns the streaming response body."""
        import boto3  # lazy: no import-time Bedrock/network side effects

        client = boto3.client("bedrock-agentcore", region_name=self._region)
        kwargs = dict(
            agentRuntimeArn=self._runtime_arn,
            runtimeSessionId=self._runtime_session_id(session_id),
            accept="text/event-stream",
            contentType="application/json",
            payload=json.dumps(
                {"sessionId": session_id, "message": message}
            ).encode("utf-8"),
        )
        if self._qualifier:
            kwargs["qualifier"] = self._qualifier
        resp = client.invoke_agent_runtime(**kwargs)
        return resp.get("response")

    async def stream(self, session_id: str, message: str) -> AsyncIterator[Dict]:
        # boto3's streaming body is blocking; run the invoke + line reads in a
        # thread and hand parsed events back to the async caller via a queue so
        # the event loop is never blocked.
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        _SENTINEL = object()

        def _pump() -> None:
            try:
                body = self._invoke(session_id, message)
                for ev in _parse_runtime_sse(body):
                    loop.call_soon_threadsafe(queue.put_nowait, ev)
            except Exception as exc:  # surface as an error frame
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "error", "message": str(exc)}
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        task = loop.run_in_executor(None, _pump)
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
        finally:
            await task


def _parse_runtime_sse(body):
    """Yield normalized event dicts from the runtime's SSE byte stream.

    Each SSE frame is a block of ``event: <type>`` and ``data: <json>`` lines
    terminated by a blank line (exactly what ``agent/app.py`` emits). We rebuild
    the normalized dict (``{"type": <type>, **data}``) the route expects.
    """
    event_type = None
    data_lines: list[str] = []

    def _emit():
        nonlocal event_type, data_lines
        if event_type is None and not data_lines:
            return None
        payload = "\n".join(data_lines).strip()
        parsed: dict = {}
        if payload:
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = {"text": payload}
        etype = event_type or parsed.get("type")
        event_type = None
        data_lines = []
        if not etype:
            return None
        return {"type": etype, **parsed}

    # iter_lines splits on newlines; a blank line delimits one SSE frame.
    for raw in body.iter_lines():
        line = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        line = line.rstrip("\r")
        if line == "":
            ev = _emit()
            if ev is not None:
                yield ev
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        # ignore comments/other SSE fields
    # Flush any trailing frame not followed by a blank line.
    ev = _emit()
    if ev is not None:
        yield ev


def get_chat_backend() -> ChatBackend:
    """Return the active chat backend (the single Phase-5 swap point).

    Selects ``RuntimeChatBackend`` (the deployed AgentCore Runtime) when
    ``CHAT_BACKEND=runtime`` OR the app is in INFRA mode (``is_local_mode()`` is
    False) and a runtime ARN is configured; otherwise ``LocalChatBackend``
    (in-process ``run_agent``) is the default/fallback. The event contract is
    identical across both.
    """
    from agent.config import load_config

    cfg = load_config()
    runtime_arn = (cfg.runtime_endpoint_arn or "").strip()
    forced = os.environ.get("CHAT_BACKEND", "").strip().lower() == "runtime"
    want_runtime = forced or (not cfg.is_local_mode())
    if want_runtime and runtime_arn:
        return RuntimeChatBackend(runtime_arn=runtime_arn, region=cfg.aws_region)
    return LocalChatBackend()
