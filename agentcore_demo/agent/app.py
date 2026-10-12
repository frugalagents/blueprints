"""FastAPI HTTP server implementing the AgentCore Runtime contract (task 2.3).

Exposes the two endpoints the AgentCore Runtime expects on port 8080:

    GET  /ping         -> 200 {"status": "Healthy"}
    POST /invocations  -> text/event-stream (SSE) relaying the agent's turn

``/invocations`` accepts JSON ``{"sessionId": str, "message": str}`` and streams
``run_agent`` normalized events as Server-Sent Events, one SSE frame per event:

    event: token      data: {"text": "<delta>"}
    event: tool_start data: {"toolUseId": "...", "name": "..."}
    event: tool_stop  data: {"toolUseId": "...", "name": "...", "status": "...", "result": ...}
    event: done       data: {"stop_reason": "...", "message": "<final text>"}   (last)
    event: error      data: {"message": "..."}                                 (on failure)

Run locally:

    .venv/bin/python -m uvicorn agent.app:app --port 8080
    # or
    .venv/bin/python agent/app.py
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

try:  # imported as a package: `uvicorn agent.app:app`
    from agent.agent import run_agent
except ImportError:  # imported from inside the agent/ directory
    from agent import run_agent

app = FastAPI(title="Advisor Copilot — Local Agent", version="0.2.0")


class InvocationRequest(BaseModel):
    """AgentCore /invocations request body."""

    sessionId: str
    message: str


@app.get("/ping")
async def ping() -> dict:
    """AgentCore health check."""
    return {"status": "Healthy"}


@app.post("/invocations")
async def invocations(req: InvocationRequest) -> EventSourceResponse:
    """Stream one agent turn back to the caller as Server-Sent Events."""

    async def event_source():
        try:
            async for ev in run_agent(req.message, req.sessionId):
                etype = ev.get("type")
                if etype == "token":
                    data = {"text": ev.get("text", "")}
                elif etype == "tool_start":
                    data = {"toolUseId": ev.get("toolUseId"), "name": ev.get("name")}
                elif etype == "tool_stop":
                    data = {
                        "toolUseId": ev.get("toolUseId"),
                        "name": ev.get("name"),
                        "status": ev.get("status"),
                        "result": ev.get("result"),
                    }
                elif etype == "policy":
                    data = {
                        "decision": ev.get("decision"),
                        "principal": ev.get("principal"),
                        "resource": ev.get("resource"),
                    }
                elif etype == "done":
                    data = {
                        "stop_reason": ev.get("stop_reason"),
                        "message": ev.get("message", ""),
                    }
                else:
                    continue
                yield {"event": etype, "data": json.dumps(data)}
        except Exception as exc:  # surface stream failures as an error frame
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}

    return EventSourceResponse(event_source())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
