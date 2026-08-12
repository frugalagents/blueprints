"""Phase 3 runtime — thin-slice chat agent per ARCHITECTURE.md §4 and
IMPLEMENTATION_PLAN.md Phase 3.

Wires the OKF graph walker, decision matrix, and fallback handler as Strands
tools around a single Agent. Deliberately scoped to the 5-component slice
(identity, microvm, mcpgw, rollback, evals) — this proves the runtime SHAPE
(retrieve → ground → fallback-if-missing → conflict-check) before Phase 4's
27-component build-out, per the plan's stated sequencing rationale.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from strands import Agent, tool
from strands.models import BedrockModel
from strands.types.tools import ToolContext

from chat.design_output import write_design_summary
from chat.fallback import run_fallback
from chat.matrix import Decision, DecisionMatrix
from chat.okf_graph import OKFGraph

# Verified live via `aws bedrock list-inference-profiles` on 2026-08-12 —
# repo convention (CLAUDE.md, custom-agent-tokenomics) requires checking a
# model ID resolves before citing/using it rather than assuming a suffix.
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-5"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge"
MATRIX_PATH = PROJECT_ROOT / "data" / "decision-matrix.yaml"
ALLOWLIST_PATH = PROJECT_ROOT / "data" / "sources.json"
GAPS_PATH = PROJECT_ROOT / "data" / "state" / "gaps.json"
DESIGNS_ROOT = PROJECT_ROOT / "designs"

_graph = OKFGraph(KNOWLEDGE_ROOT)
_matrix = DecisionMatrix(MATRIX_PATH)
# Decisions are scoped per-Agent instance via agent.state (a per-agent
# JSON-serializable store Strands provides), NOT a module-level global — an
# earlier version of this file used a module global and leaked decisions
# across separate Agent instances/conversations. See IMPLEMENTATION_PLAN.md
# Phase 3 test notes (2026-08-12) for how that was caught.
DECISIONS_STATE_KEY = "decisions_log"


def _get_decisions_log(agent: Agent) -> list[Decision]:
    raw = agent.state.get(DECISIONS_STATE_KEY) or []
    return [Decision(component_id=d["component_id"], option=d["option"]) for d in raw]


def _save_decisions_log(agent: Agent, log: list[Decision]) -> None:
    agent.state.set(
        DECISIONS_STATE_KEY,
        [{"component_id": d.component_id, "option": d.option} for d in log],
    )


PROFILE_STATE_KEY = "org_profile"


def _stamp_last_gap() -> None:
    """chat/fallback.py's log_gap() intentionally omits a timestamp (no clock
    access there by design) — the runtime stamps the most recent entry here.
    """
    if not GAPS_PATH.exists():
        return
    entries = json.loads(GAPS_PATH.read_text(encoding="utf-8"))
    if entries and "timestamp" not in entries[-1]:
        entries[-1]["timestamp"] = datetime.now(timezone.utc).isoformat()
        GAPS_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


@tool
def lookup_component(component_id: str) -> str:
    """Retrieve a platform component's full content plus its 1-hop graph
    neighbors from the curated knowledge base. Use this FIRST for any
    question about a specific platform component (e.g. 'identity', 'microvm',
    'mcpgw', 'rollback', 'evals'). If this returns found=false, the component
    is not yet in the curated wiki — call fallback_answer next instead of
    answering from general knowledge.

    Args:
        component_id: the component's slug id, e.g. "mcpgw" or "identity".
    """
    result = _graph.retrieve(component_id)
    if not result["found"]:
        return json.dumps({"found": False, "component_id": component_id})

    comp = result["component"]
    neighbors = [
        {"id": n.id, "title": n.title, "description": n.description}
        for n in result["neighbors"]
    ]
    return json.dumps({
        "found": True,
        "id": comp.id,
        "title": comp.title,
        "status": comp.status,
        "content": comp.full_text,
        "neighbors": neighbors,
    })


@tool
def fallback_answer(query: str, source_url: str, matched_component_id: str = "") -> str:
    """Use ONLY when lookup_component returned found=false for every
    component relevant to the user's question — i.e. the curated wiki has no
    answer. Fetches from a fixed, pre-approved allowlist ONLY (never open
    web), filters the content for injected instructions before returning it,
    and logs the gap so it can inform future wiki growth. You MUST label your
    answer to the user as sourced live, not from the curated wiki, using the
    returned label text verbatim.

    Args:
        query: the user's original question, verbatim.
        source_url: a URL from the allowlist to fetch. Only these are valid,
            fetch attempts against any other URL will be rejected:
            https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.md
            https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.md
            https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.md
        matched_component_id: leave empty if no component matched at all.
    """
    try:
        result = run_fallback(
            query=query,
            url=source_url,
            allowlist_path=ALLOWLIST_PATH,
            gaps_path=GAPS_PATH,
            matched_component_id=matched_component_id or None,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})
    _stamp_last_gap()
    return json.dumps({
        "label": result.label,
        "content": result.content,
        "source_url": result.answer_source_url,
    })


@tool(context=True)
def record_decision(component_id: str, option: str, tool_context: ToolContext) -> str:
    """Record a design decision the user has made for a component, and check
    it against every other decision made so far for conflicts. ALWAYS call
    this immediately after the user picks or confirms an option for a
    component's decision — not just at the end of the conversation. If a
    conflict is returned, surface it to the user in this same turn.

    Args:
        component_id: e.g. "rollback", "evals", "identity".
        option: a short slug for the option chosen, e.g.
            "direct-commits-to-working-branch", "advisory-only-non-blocking",
            "agent-acts-as-developer". Use the option's meaning, not its full
            prose text.
    """
    agent = tool_context.agent
    log = _get_decisions_log(agent)
    log.append(Decision(component_id, option))
    _save_decisions_log(agent, log)
    hits = _matrix.check_conflicts(log)
    return json.dumps({
        "recorded": {"component_id": component_id, "option": option},
        "decisions_so_far": [{"component_id": d.component_id, "option": d.option} for d in log],
        "conflicts": [{"id": h.id, "warning": h.warning} for h in hits],
    })


@tool(context=True)
def list_decisions(tool_context: ToolContext) -> str:
    """List every design decision recorded so far in THIS conversation, and
    report any conflicts among them. Use this when the user asks what's been
    decided so far, or asks you to summarize/review.
    """
    agent = tool_context.agent
    log = _get_decisions_log(agent)
    hits = _matrix.check_conflicts(log)
    return json.dumps({
        "decisions_so_far": [{"component_id": d.component_id, "option": d.option} for d in log],
        "conflicts": [{"id": h.id, "warning": h.warning} for h in hits],
    })


@tool(context=True)
def resolve_profile(
    org_size: str, compliance: str, existing_stack: str, autonomy: str,
    data_sensitivity: str, tool_context: ToolContext,
) -> str:
    """Given the user's answers to the 5 org-profile axis questions, return
    which of the currently-loaded components are required vs optional, and in
    what priority order to discuss them. Call this once, early, after asking
    the user these 5 questions. The profile is remembered for this
    conversation so generate_design_summary can include it later.

    Args:
        org_size: one of solo-team, multi-team, org-wide.
        compliance: one of none, soc2, regulated.
        existing_stack: one of greenfield, has-sso-scm, has-competing-tool.
        autonomy: one of advisory-only, gated-writes, full-auto-sandbox.
        data_sensitivity: one of public, internal, regulated-data.
    """
    profile = {
        "org_size": org_size,
        "compliance": compliance,
        "existing_stack": existing_stack,
        "autonomy": autonomy,
        "data_sensitivity": data_sensitivity,
    }
    tool_context.agent.state.set(PROFILE_STATE_KEY, profile)
    ranked = _matrix.resolve(profile)
    return json.dumps([
        {"component_id": r.component_id, "relevance": r.relevance, "priority": r.priority}
        for r in ranked
    ])


@tool(context=True)
def generate_design_summary(org_slug: str, tool_context: ToolContext) -> str:
    """Generate the final platform design document for this conversation —
    an OKF-format markdown file under designs/ linking back to every
    component decided on, plus the org profile (if resolve_profile was
    called) and any conflicts flagged along the way. Call this when the user
    asks for a summary/writeup/design doc, or indicates the conversation is
    wrapping up. This does NOT restate each component's rationale — it links
    to the source component in knowledge/ instead, so the summary can't drift
    from the source content.

    Args:
        org_slug: a short identifier for this session/org, e.g.
            "acme-corp" or "regulated-fintech-poc". Used as the output
            filename.
    """
    agent = tool_context.agent
    log = _get_decisions_log(agent)
    profile = agent.state.get(PROFILE_STATE_KEY)
    path = write_design_summary(
        org_slug=org_slug,
        decisions=log,
        graph=_graph,
        matrix=_matrix,
        designs_root=DESIGNS_ROOT,
        profile=profile,
    )
    return json.dumps({
        "written_to": str(path.relative_to(PROJECT_ROOT)),
        "decision_count": len(log),
        "had_profile": profile is not None,
    })


SYSTEM_PROMPT = """You are a coding agent platform design advisor. You help \
users design a fit-for-purpose coding agent platform by walking through \
platform components (identity, execution sandboxing, tool gateways, \
rollback/change-safety, evals) grounded strictly in a curated knowledge base.

Rules, no exceptions:
1. For any question about a specific platform component, call lookup_component \
first. Ground your answer in what it returns — the component's own content \
and its 1-hop neighbors. Never answer from general knowledge if a component \
match exists.
2. If lookup_component returns found=false, or the matched component's \
content doesn't actually address the question, call fallback_answer. Present \
its returned label to the user VERBATIM before or alongside the answer — \
never blend a fallback answer into your response as if it were curated \
wiki content.
3. A component's status may be "candidate" — present that content but note \
explicitly that it hasn't been fully corroborated yet, don't state it with \
the same confidence as a "stable" component.
4. Whenever the user picks or confirms a decision for a component, call \
record_decision immediately. If it returns any conflicts, explain the \
conflict to the user in this same turn — do not wait until later in the \
conversation.
5. Never assert a claim that isn't traceable to what a tool call returned to \
you in this conversation.
6. If the user asks what's been decided so far, or asks for a summary/review, \
call list_decisions rather than reconstructing it from memory of the \
conversation — it also re-checks for conflicts across everything recorded.
7. When the user asks for the final design/writeup, or signals they're done, \
call generate_design_summary to produce the actual deliverable file. Don't \
just paste a summary into the chat as the final answer — this tool writes an \
OKF-format design document under designs/ that a human can keep, and it \
avoids restating component rationale by linking back to knowledge/ instead.
"""


def build_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=BEDROCK_MODEL_ID),
        system_prompt=SYSTEM_PROMPT,
        tools=[
            lookup_component, fallback_answer, record_decision, list_decisions,
            resolve_profile, generate_design_summary,
        ],
    )
