"""Local-first Strands agent for the Advisor Copilot POC (task 2.1).

Wraps the seven synthetic mock tools (portfolio / market / research) as Strands
tools, builds a Bedrock-backed ``Agent`` with a compliance-oriented
financial-advisor-assistant system prompt, and conditionally attaches the real
Bedrock Guardrail (FEAT-001) when its id/version are configured. When no
guardrail id is present the agent still runs (local mode).

The async entry point ``run_agent(message, session_id, history=None)`` streams
the model via ``stream_async`` and yields a small set of *normalized* event
dicts (token / tool_start / tool_stop / done) so the FastAPI SSE server
(``agent/app.py``) and verification scripts share one event shape. Conversation
memory is kept in the in-process ``SESSIONS`` shim (``agent/memory.py``) so a
follow-up turn in the same session retains client context without restating it.

CLI smoke test (makes a real Bedrock call):

    AWS_REGION=us-east-1 .venv/bin/python -c \
        "import asyncio; from agent.agent import _smoke; asyncio.run(_smoke())"
"""

from __future__ import annotations

import json
import os
import sys
from typing import AsyncIterator, Dict, List, Optional

# Load .env (real guardrail id/version) if python-dotenv is available. Optional:
# the agent must still run when the package or file is absent (pure local mode).
try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except Exception:  # pragma: no cover
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from strands import Agent, tool
from strands.models import BedrockModel

try:  # imported as a package: `from agent.config import ...`
    from agent.config import Config, load_config
    from agent.memory import SESSIONS
except ImportError:  # imported from inside the agent/ directory
    from config import Config, load_config
    from memory import SESSIONS

# Make the sibling mocks/ package importable as a top-level module, then import
# the local tool adapters (which themselves reuse the *_lambda core logic).
_MOCKS_DIR = os.path.join(_REPO_ROOT, "mocks")
if _MOCKS_DIR not in sys.path:
    sys.path.insert(0, _MOCKS_DIR)

import local_tools  # noqa: E402  (path adjusted above)


# --------------------------------------------------------------------------- #
# Tool wrappers — thin @tool shims over the local mock adapters. Type hints +
# the one-line docstring become each tool's input schema and description.
# --------------------------------------------------------------------------- #

@tool
def get_portfolio(client_id: str) -> dict:
    """Return the full client/portfolio record (name, risk profile, advisor, positions, concentration flags) for a client id such as 'C-1001'."""
    return local_tools.get_portfolio(client_id)


@tool
def get_positions(client_id: str) -> list:
    """Return the list of holdings (ticker, shares, cost basis, sector) for a client id such as 'C-1001'."""
    return local_tools.get_positions(client_id)


@tool
def get_concentration_flags(client_id: str) -> list:
    """Return the concentration-risk flags for a client id such as 'C-1001' (e.g. an outsized single-name position)."""
    return local_tools.get_concentration_flags(client_id)


@tool
def get_quote(ticker: str) -> dict:
    """Return a snapshot market quote (last price, percent change, P/E, as-of time) for a single ticker such as 'NVDA'."""
    return local_tools.get_quote(ticker)


@tool
def get_quotes(tickers: str) -> list:
    """Return snapshot market quotes for several tickers given as a comma-separated string such as 'NVDA,AGG,MSFT'."""
    parts = [t.strip() for t in tickers.split(",") if t.strip()]
    return local_tools.get_quotes(parts)


@tool
def list_research(ticker: str) -> list:
    """Return internal research notes for a ticker such as 'NVDA', newest first; each note includes its note id (e.g. 'R-5567'), rating, and price target."""
    return local_tools.list_research(ticker)


@tool
def get_research(note_id: str) -> dict:
    """Return a single internal research note by its note id such as 'R-5567' (analyst, rating, price target, summary, disclaimer flag)."""
    return local_tools.get_research(note_id)


TOOLS = [
    get_portfolio,
    get_positions,
    get_concentration_flags,
    get_quote,
    get_quotes,
    list_research,
    get_research,
]


SYSTEM_PROMPT = """\
You are the Advisor Copilot, an assistant for a licensed financial \
advisor (FA). You help the advisor prepare for client conversations using the \
firm's internal tools. You assist the advisor; you never act as the client's \
advisor directly and you never execute trades or transactions.

Grounding and sourcing rules:
- Ground EVERY factual claim (holdings, prices, ratings, price targets, \
concentration flags) in the output of the provided tools. Call the tools to get \
portfolio data, market quotes, and research notes before stating figures.
- Never invent, estimate, or round figures that a tool did not return. If a tool \
has no data, say so plainly rather than guessing.
- When you reference internal research, cite the research note id (for example \
R-5567) so the advisor can trace the source.

Compliance rules:
- Never promise, guarantee, or imply guaranteed, riskless, or "can't-lose" \
returns. All investments carry risk, including possible loss of principal.
- Do not give definitive personalized tax or legal advice; suggest the advisor \
consult the appropriate licensed professional.
- Do not solicit or use material non-public (insider) information.
- Advise and prepare talking points only. Never place, confirm, or promise to \
execute trades.

Always close substantive investment commentary with a brief disclaimer that this \
is for the advisor's internal preparation, is not a guarantee of future results, \
and that investments carry risk including possible loss of principal.
"""


def build_model(cfg: Config) -> BedrockModel:
    """Build the BedrockModel, attaching the guardrail only when configured."""
    kwargs = dict(model_id=cfg.bedrock_model_id, region_name=cfg.aws_region)
    if cfg.guardrail_id:
        kwargs.update(
            guardrail_id=cfg.guardrail_id,
            guardrail_version=cfg.guardrail_version or "DRAFT",
            guardrail_trace="enabled",
        )
    return BedrockModel(**kwargs)


def _history_to_messages(history: Optional[List[Dict[str, str]]]) -> list:
    """Convert memory turns to Strands/Bedrock message shape for seeding."""
    messages = []
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": [{"text": content}]})
    return messages


def build_agent(cfg: Optional[Config] = None,
                history: Optional[List[Dict[str, str]]] = None,
                tools: Optional[list] = None) -> Agent:
    """Construct a Strands Agent, optionally seeded with prior conversation turns.

    ``tools`` lets a caller inject the Gateway MCP tools (INFRA mode); when it is
    None the local ``@tool`` adapters (``TOOLS``) are used (local dev / fallback).
    """
    cfg = cfg or load_config()
    return Agent(
        model=build_model(cfg),
        tools=TOOLS if tools is None else tools,
        system_prompt=SYSTEM_PROMPT,
        messages=_history_to_messages(history),
        callback_handler=None,
    )


# --------------------------------------------------------------------------- #
# Stream-event normalization. Shared by run_agent / app.py / verification so the
# SSE frame shapes stay consistent. See phase2-plan.md for the confirmed
# stream_async event keys.
# --------------------------------------------------------------------------- #

def _summarize_tool_result(content_block: dict) -> Dict[str, object]:
    """Pull a json-safe {status, result} from a user toolResult content block."""
    tool_result = content_block.get("toolResult", {})
    status = tool_result.get("status", "success")
    # toolResult.content is a list of blocks; surface text / json payloads.
    parts = []
    for block in tool_result.get("content", []) or []:
        if "json" in block:
            parts.append(block["json"])
        elif "text" in block:
            parts.append(block["text"])
    result: object
    if len(parts) == 1:
        result = parts[0]
    elif parts:
        result = parts
    else:
        result = None
    return {"status": status, "result": result}


# Tool-name substrings that identify a PORTFOLIO (CAP-H gated) tool call. The
# Gateway advertises the portfolio tool as "<target>___getClientPortfolio"; the
# local adapters are get_portfolio / get_positions / get_concentration_flags.
_PORTFOLIO_TOOL_MARKERS = (
    "getclientportfolio",
    "get_portfolio",
    "get_positions",
    "get_concentration_flags",
)

ACTING_ADVISOR_DEFAULT = "FA-77"


def _is_portfolio_tool(name: str) -> bool:
    low = (name or "").lower()
    return any(marker in low for marker in _PORTFOLIO_TOOL_MARKERS)


def _extract_client_id(tool_input: object) -> str:
    """Best-effort client_id from a (possibly partial) tool_use input.

    The streamed ``input`` may be a dict or a partial JSON string being built up
    token-by-token. We try a strict JSON parse, then a loose regex for
    ``"client_id": "C-xxxx"`` so the resource is captured even mid-stream.
    """
    import re

    if isinstance(tool_input, dict):
        val = tool_input.get("client_id")
        return str(val) if val else ""
    if isinstance(tool_input, str) and tool_input.strip():
        try:
            parsed = json.loads(tool_input)
            if isinstance(parsed, dict) and parsed.get("client_id"):
                return str(parsed["client_id"])
        except ValueError:
            pass
        m = re.search(r'client_id"\s*:\s*"([^"]+)"', tool_input)
        if m:
            return m.group(1)
    return ""


def _policy_decision_from_result(status: str, result: object) -> str:
    """Classify a portfolio tool_stop into an AgentCore Policy decision.

    The Policy Engine is attached to the Gateway in ENFORCE mode, so a non-owned
    client's portfolio call is DENIED server-side at the Gateway and surfaces as
    a tool error whose text mentions policy enforcement / denial. A successful
    call means the Policy ALLOWED it. This derivation is the ONLY source of the
    policy event — it reflects the REAL gateway authorization outcome, not a
    client-side guess.
    """
    text = json.dumps(result) if not isinstance(result, str) else result
    low = text.lower()
    denied_markers = (
        "policy enforcement",
        "policy evaluation denied",
        "tool execution denied",
        "not allowed due to policy",
        "authorizeaction",
        "denied by default",
    )
    if status != "success" or any(m in low for m in denied_markers):
        return "DENIED"
    return "ALLOWED"


async def normalize_stream(agent: Agent, message: str,
                           acting_advisor: str = ACTING_ADVISOR_DEFAULT
                           ) -> AsyncIterator[dict]:
    """Yield normalized token/tool_start/tool_stop/policy/done events.

    The ``policy`` event is ADDITIVE to the existing token/tool_start/tool_stop/
    done contract: for every PORTFOLIO tool call it emits exactly one
    ``{"type":"policy","decision":"ALLOWED"|"DENIED","principal":<advisor>,
    "resource":<client_id>}`` derived from the Gateway's real enforcement outcome
    on that tool's ``tool_stop``.
    """
    seen_tools: Dict[str, str] = {}  # toolUseId -> name (for tool_start dedupe)
    tool_resources: Dict[str, str] = {}  # toolUseId -> client_id (resource)

    async for event in agent.stream_async(message):
        # Text delta.
        if "data" in event and isinstance(event["data"], str):
            yield {"type": "token", "text": event["data"]}
            continue

        # Tool-use streaming: first sighting of a toolUseId => tool_start.
        if event.get("type") == "tool_use_stream":
            current = event.get("current_tool_use") or {}
            tool_use_id = current.get("toolUseId")
            name = current.get("name")
            # Capture the client_id argument as it accumulates so the policy
            # event can name the resource. `input` is a JSON string being built.
            if tool_use_id:
                cid = _extract_client_id(current.get("input"))
                if cid:
                    tool_resources[tool_use_id] = cid
            if tool_use_id and tool_use_id not in seen_tools:
                seen_tools[tool_use_id] = name or ""
                yield {"type": "tool_start", "toolUseId": tool_use_id, "name": name}
            continue

        # Message boundaries: a user message carrying a toolResult => tool_stop.
        msg = event.get("message")
        if isinstance(msg, dict) and msg.get("role") == "user":
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and "toolResult" in block:
                    tool_use_id = block["toolResult"].get("toolUseId")
                    summary = _summarize_tool_result(block)
                    name = seen_tools.get(tool_use_id or "")
                    yield {
                        "type": "tool_stop",
                        "toolUseId": tool_use_id,
                        "name": name,
                        "status": summary["status"],
                        "result": summary["result"],
                    }
                    # Policy event: one per portfolio tool call, from the REAL
                    # gateway enforcement outcome on this result.
                    if _is_portfolio_tool(name):
                        decision = _policy_decision_from_result(
                            summary["status"], summary["result"]
                        )
                        yield {
                            "type": "policy",
                            "decision": decision,
                            "principal": acting_advisor,
                            "resource": tool_resources.get(tool_use_id or "", ""),
                        }
            continue

        # Final result => done.
        if "result" in event:
            result = event["result"]
            stop_reason = getattr(result, "stop_reason", None)
            final_text = str(result)
            yield {
                "type": "done",
                "stop_reason": stop_reason,
                "message": final_text,
            }
            continue


async def run_agent(message: str,
                    session_id: str,
                    history: Optional[List[Dict[str, str]]] = None,
                    cfg: Optional[Config] = None) -> AsyncIterator[dict]:
    """Run one turn: seed from session memory, stream, and persist the result.

    Appends the user turn to the session store before streaming, seeds the
    agent with the prior conversation (from ``history`` or the session store),
    yields normalized events, and appends the assistant's final text once the
    ``done`` event arrives. Yields the same event dicts ``app.py`` relays as SSE.

    Tool source: in INFRA mode (``cfg.use_gateway_tools()``) the agent calls the
    five Gateway MCP tools over a SigV4-signed MCP client; otherwise it uses the
    local ``@tool`` adapters (local dev / fallback).
    """
    cfg = cfg or load_config()

    # Seed prior context from the explicit history or the session store (taken
    # BEFORE this turn's user message is appended).
    seed = history if history is not None else SESSIONS.get_history(session_id)

    SESSIONS.append_turn(session_id, "user", message)

    final_text = ""
    if cfg.use_gateway_tools():
        # INFRA mode: discover + use the Gateway MCP tools. The MCPClient keeps a
        # background session open; the whole turn runs inside its context.
        try:
            from agent.mcp_gateway import build_gateway_mcp_client
        except ImportError:  # imported from inside the agent/ directory
            from mcp_gateway import build_gateway_mcp_client

        mcp_client = build_gateway_mcp_client(cfg.gateway_mcp_url, cfg.aws_region)
        with mcp_client:
            gateway_tools = mcp_client.list_tools_sync()
            agent = build_agent(cfg=cfg, history=seed, tools=gateway_tools)
            async for ev in normalize_stream(agent, message):
                if ev["type"] == "done":
                    final_text = ev.get("message") or ""
                yield ev
    else:
        agent = build_agent(cfg=cfg, history=seed)
        async for ev in normalize_stream(agent, message):
            if ev["type"] == "done":
                final_text = ev.get("message") or ""
            yield ev

    SESSIONS.append_turn(session_id, "assistant", final_text)


# --------------------------------------------------------------------------- #
# CLI smoke test — the scripted Jane Doe prep prompt. One real Bedrock call.
# --------------------------------------------------------------------------- #

JANE_DOE_PROMPT = (
    "Help me prep for a review with my client Jane Doe (client id C-1001). "
    "Pull her portfolio and any concentration flags, get the latest market "
    "quote for her largest position NVDA, and summarize our most recent "
    "internal NVDA research note with its note id. Give me talking points."
)


async def _smoke() -> None:
    """Run the Jane Doe prompt and print normalized events for verification."""
    session_id = "smoke-session"
    tools_used: List[str] = []
    print("=== prompt ===")
    print(JANE_DOE_PROMPT)
    print("\n=== stream ===")
    final = ""
    async for ev in run_agent(JANE_DOE_PROMPT, session_id):
        etype = ev["type"]
        if etype == "token":
            print(ev["text"], end="", flush=True)
        elif etype == "tool_start":
            tools_used.append(ev.get("name") or "")
            print(f"\n[tool_start] {ev.get('name')} ({ev.get('toolUseId')})")
        elif etype == "tool_stop":
            print(f"\n[tool_stop]  {ev.get('name')} status={ev.get('status')}")
        elif etype == "done":
            final = ev.get("message") or ""
            print(f"\n\n[done] stop_reason={ev.get('stop_reason')}")
    print("\n=== tools invoked ===")
    print(tools_used)
    print("=== cites R-5567 ===", "R-5567" in final)


if __name__ == "__main__":
    import asyncio

    asyncio.run(_smoke())
