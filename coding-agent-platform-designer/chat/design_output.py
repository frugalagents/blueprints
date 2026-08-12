"""Final design output — ARCHITECTURE.md §4: 'Decisions log emitted as a new
OKF file: designs/<org-slug>.md, linking back to every component decided on
— the output dogfoods the same format as the input.'

This closes a real gap found by direct inspection (2026-08-12, before Phase
4): record_decision/list_decisions accumulated a flat decisions log, but
nothing ever assembled it into the actual deliverable a user leaves the
conversation with. ARCHITECTURE.md §7 item 2 flags the output SHAPE as an
open question (plain list vs. list+rationale vs. diagram) — this is a
deliberately minimal first cut: link back to source components rather than
duplicating their prose, since OKF's own component files are the source of
truth for rationale, and duplicating text out of them would create a second
copy that can drift when a component's content changes.
"""

from __future__ import annotations

import re
from pathlib import Path

from chat.matrix import Decision, DecisionMatrix
from chat.okf_graph import OKFGraph

SLUG_RE = re.compile(r"[^a-z0-9]+")
MERMAID_ID_RE = re.compile(r"[^a-zA-Z0-9_]")


def slugify(text: str) -> str:
    return SLUG_RE.sub("-", text.lower()).strip("-") or "session"


def _mermaid_id(component_id: str) -> str:
    """Mermaid node IDs can't contain hyphens reliably across renderers;
    component ids in this project are already hyphen-free slugs, but sanitize
    defensively rather than assume.
    """
    return MERMAID_ID_RE.sub("_", component_id)


def render_mermaid_diagram(decisions: list[Decision], graph: OKFGraph) -> str:
    """Renders the SPECIFIC components this user decided on, as a Mermaid
    flowchart — grouped by OKF group (subgraphs), labeled with the option
    chosen, edges drawn only from real '## Connects to' links in
    knowledge/ that are also decided components in this session (an edge to
    an undecided component would draw a relationship the user never actually
    discussed). This is deliberately NOT a rendering of the full 32-component
    reference architecture — it's this conversation's design, only.

    Returns an empty string if there are no decisions (caller should skip the
    diagram section entirely in that case).
    """
    if not decisions:
        return ""

    decided_ids = {d.component_id for d in decisions}
    option_by_id = {d.component_id: d.option for d in decisions}

    groups: dict[str, list[str]] = {}
    for comp_id in decided_ids:
        comp = graph.get(comp_id)
        group = comp.group if comp else "unknown"
        groups.setdefault(group, []).append(comp_id)

    lines = ["```mermaid", "flowchart TD"]

    for group, comp_ids in sorted(groups.items()):
        lines.append(f'    subgraph {_mermaid_id(group)} ["{group}"]')
        for comp_id in sorted(comp_ids):
            comp = graph.get(comp_id)
            title = comp.title if comp else comp_id
            option = option_by_id[comp_id]
            status_suffix = f" [{comp.status}]" if comp and comp.status != "stable" else ""
            label = f"{title}<br/>{option}{status_suffix}".replace('"', "'")
            lines.append(f'        {_mermaid_id(comp_id)}["{label}"]')
        lines.append("    end")

    # Two components may each independently declare a '## Connects to' link
    # to the other (e.g. identity says "entitles calls through mcpgw", mcpgw
    # says "enforces entitlements set by identity") — both describing the
    # same real relationship from their own side. Drawn literally that's two
    # opposite arrows, which reads as a cycle. Collapse any pair where both
    # directions exist into one undirected edge; keep one-directional edges
    # as directed arrows, since those aren't mutually declared.
    directed_pairs = set()
    for comp_id in decided_ids:
        comp = graph.get(comp_id)
        if not comp:
            continue
        for neighbor_id in comp.neighbor_ids:
            if neighbor_id in decided_ids:
                directed_pairs.add((comp_id, neighbor_id))

    drawn = set()
    for a, b in sorted(directed_pairs):
        if (a, b) in drawn or (b, a) in drawn:
            continue
        if (b, a) in directed_pairs:
            lines.append(f"    {_mermaid_id(a)} --- {_mermaid_id(b)}")
        else:
            lines.append(f"    {_mermaid_id(a)} --> {_mermaid_id(b)}")
        drawn.add((a, b))

    lines.append("```")
    return "\n".join(lines)


def render_design_summary(
    org_slug: str,
    decisions: list[Decision],
    graph: OKFGraph,
    matrix: DecisionMatrix,
    profile: dict[str, str] | None = None,
) -> str:
    """Renders an OKF-conformant markdown document: frontmatter + body,
    linking back to every decided component rather than restating its
    content. Deliberately does not try to reconstruct which prose option a
    recorded slug matches — the component's own '## Decisions' section is
    the source of truth for that, and slug-to-prose matching would be
    fragile given OKF's plain-prose-not-structured-data choice (see
    ARCHITECTURE.md §2.5). Links suffice; duplicating the text would drift.
    """
    lines = [
        "---",
        "type: design-summary",
        f"title: Platform Design Summary — {org_slug}",
        "description: decisions recorded during a design conversation",
        "tags: [design-summary]",
        "status: draft",
        "---",
        "",
        "# Platform Design Summary",
        "",
        "Generated from a conversation with the coding agent platform designer "
        "chat tool. Each decision below links back to its source component in "
        "`knowledge/` — read the component file for the full rationale behind "
        "each option; this document does not restate it, to avoid a copy that "
        "can drift from the source.",
        "",
    ]

    if profile:
        lines.append("## Org Profile")
        lines.append("")
        for axis, value in profile.items():
            lines.append(f"- **{axis}**: {value}")
        lines.append("")

    lines.append("## Decisions")
    lines.append("")
    if not decisions:
        lines.append("No decisions were recorded in this session.")
    else:
        for d in decisions:
            comp = graph.get(d.component_id)
            if comp:
                rel_path = Path("..") / "knowledge" / comp.group / f"{comp.id}.md"
                status_note = f" (status: {comp.status})" if comp.status != "stable" else ""
                lines.append(f"- [{comp.title}]({rel_path.as_posix()}){status_note} → chosen: `{d.option}`")
            else:
                lines.append(f"- {d.component_id} (not found in knowledge base) → chosen: `{d.option}`")
    lines.append("")

    diagram = render_mermaid_diagram(decisions, graph)
    if diagram:
        lines.append("## Architecture Diagram")
        lines.append("")
        lines.append(
            "Reflects only the components decided in this session — not the "
            "full reference taxonomy. Edges shown are real `## Connects to` "
            "relationships from `knowledge/`, limited to components both "
            "ends of which were actually decided here."
        )
        lines.append("")
        lines.append(diagram)
        lines.append("")

    hits = matrix.check_conflicts(decisions)
    lines.append("## Conflicts Flagged During This Session")
    lines.append("")
    if not hits:
        lines.append("None.")
    else:
        for h in hits:
            lines.append(f"- **{h.id}**: {h.warning}")
    lines.append("")

    return "\n".join(lines)


def write_design_summary(
    org_slug: str,
    decisions: list[Decision],
    graph: OKFGraph,
    matrix: DecisionMatrix,
    designs_root: Path,
    profile: dict[str, str] | None = None,
) -> Path:
    designs_root.mkdir(parents=True, exist_ok=True)
    content = render_design_summary(org_slug, decisions, graph, matrix, profile)
    out_path = designs_root / f"{slugify(org_slug)}.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path
