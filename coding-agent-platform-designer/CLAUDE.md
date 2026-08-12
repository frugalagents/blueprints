# Coding Agent Platform Designer — project conventions

This project is content-as-data, like its sibling `tokenomics/custom-agent-tokenomics/`,
but the content is *advisory/decision* knowledge (architecture tradeoffs), not
verifiable facts — see `ARCHITECTURE.md` for the full design and
`IMPLEMENTATION_PLAN.md` for build order. Read both before making structural
changes; this file covers day-to-day operational conventions only.

## Layout

- `knowledge/` — the OKF (Open Knowledge Format) bundle. One markdown file per
  platform component, grouped into 9 folders (`surfaces/`, `access/`,
  `registry/`, `harness/`, `exec/`, `gateway/`, `external/`, `ops/`, `quality/`).
  Each group folder has an `index.md`. This is Layer 1 — see ARCHITECTURE.md §2.
- `data/decision-matrix.yaml` — Layer 2. Org-profile axes → component
  relevance/order, plus `conflicts:` rules. Structured YAML, deliberately NOT an
  OKF file — this is control logic the runtime evaluates deterministically, not
  knowledge content to RAG over.
- `data/sources.json` — Tier A monitoring list, Tier B discovery seeds, Tier C
  case-study seeds (with `reviewAfterMonths`), and the fallback allowlist.
- `data/state/monitoring-state.json` — Tier A last-seen hash/date. Written by
  the monitoring pipeline, not by hand.
- `data/state/watchlist.json` — Tier B sub-threshold candidates. Written by the
  discovery pipeline, not by hand.
- `data/state/gaps.json` — runtime fallback log (query, matched component or
  null, timestamp). Written by the chat runtime when it falls back to live
  sources. Feeds Tier B/C seed queries — read it before running a discovery
  sweep.
- `drafts/` — proposed new/updated component `.md` files awaiting human review.
  Nothing here is live content. Never copy a draft into `knowledge/` without a
  human reading it first — every claim must carry a source before it ships.
- `chat/` — the runtime: agent loop, decision-matrix evaluator, OKF graph
  walker/RAG, fallback handler.
- `scripts/check-sources.mjs` — Tier A hash-diff (adapted from tokenomics: hashes
  markdown body sections instead of JSON fields).
- `scripts/manage-discovery.mjs` — Tier B watchlist/drafts CLI.
- `scripts/validate-links.mjs` — walks every `## Connects to` link in
  `knowledge/` and fails if any target file doesn't exist. OKF does not enforce
  link integrity itself; this is our own guard. Run after any change under
  `knowledge/`.

## OKF component file schema

```markdown
---
type: platform-component
title: Human title
description: one-line summary
group: one of the 9 group ids
tags: [kebab-case, tags]
timestamp: YYYY-MM-DDTHH:MM:SSZ
status: stable | candidate | deprecated
---

1-2 paragraph summary of what this component is and why it exists.

## Decisions

**<Question>?**
- <Option> — <tradeoff, 1 line>
- <Option> — <tradeoff, 1 line>

## Principles

- <short imperative guidance, 1 line each>

## Connects to

- <edge-phrase> [<Component Title>](../group/id.md)

## Sources

- [<title>](<url>) — checked YYYY-MM-DD — supports: <what this source backs>
```

`## Decisions` stays plain markdown prose, never a fenced YAML/JSON block —
matches OKF's "minimally opinionated" principle; the chat agent reads it via
RAG, no parser contract to keep in sync. This is a deliberate, confirmed choice
(see ARCHITECTURE.md §2.5) — don't restructure it into structured data without
re-opening that decision.

## Edge vocabulary (convention, not enforced by OKF)

Use these verb phrases in `## Connects to` so the graph stays legible and
walkable by the runtime's 1-hop traversal. Don't invent new phrasing per file.

| Phrase | Meaning | Example |
|---|---|---|
| "governed by / entitled by" | control-plane dependency | tools → identity |
| "routes through / brokers via" | mandatory chokepoint | landscape → mcpgw |
| "feeds / emits to" | telemetry flow | mcpgw → observability |
| "invoked by" | caller relationship | tools ← runtime |
| "loaded from" | registry/catalog sourcing | skills → registry |

If a relationship doesn't fit one of these, it's fine to write a new phrase —
but check first whether it's actually one of the above stated differently.

## Component taxonomy

32 components, 9 groups. Full table and rationale for every split/addition is
in ARCHITECTURE.md §2.2–§2.3 — do not re-derive it from `coding-agent-platform-arch.html`
directly; that HTML is historical origin only (25 baseline components), not the
current source of truth. `ARCHITECTURE.md` + `knowledge/` are authoritative.

If the taxonomy changes again (split/merge/rename/promote a Tier B candidate),
update ARCHITECTURE.md §2 first, then `knowledge/`, then note the change in a
`log.md`. Never let the two drift silently.

## Fallback (runtime behavior, not a data rule, but affects sourcing)

When the chat runtime can't answer from `knowledge/`, it falls back to a live
fetch scoped to `data/sources.json`'s fallback allowlist only — never open web.
Every fallback fetch passes through content-injection filtering before reaching
the model, no exception for allowlisted domains (see ARCHITECTURE.md §4.1).
Fallback answers are always labeled to the user as not-yet-curated. Every
fallback hit is logged to `data/state/gaps.json`.

## Chat runtime conventions (`chat/`)

Built on Strands Agents SDK on Bedrock (`us.anthropic.claude-sonnet-5` —
verify any model ID resolves via `aws bedrock list-inference-profiles` before
using or citing it; don't assume a suffix is current).

**Conversation-scoped state must live in `agent.state`, never a module-level
variable.** Phase 3 testing (see `IMPLEMENTATION_PLAN.md`) found and fixed a
real bug where the decisions log was a module global — two separate `Agent`
instances (i.e. two separate conversations) shared and leaked state into each
other. The fix: any tool that reads/writes conversation-scoped data takes
`@tool(context=True)` and a `tool_context: ToolContext` parameter, then reads
`tool_context.agent.state` (a per-agent JSON-serializable store Strands
provides) — see `chat/agent.py`'s `record_decision`/`list_decisions` for the
pattern. A plain module-level list/dict will pass a single-conversation test
and silently fail the moment two conversations run in the same process.

## Rules for any agent (human or automated) touching this project

1. Never write directly to `knowledge/*.md` from an automated pipeline (Tier A,
   B, or C). Automated output goes to `drafts/`. A human promotes a draft after
   reading it.
2. Every claim in a component's body must be traceable to an entry in that
   file's `## Sources`. If you can't cite it, say "verify against current docs"
   instead of asserting it.
3. Tier C (case-study) sources must state "as of `<date>`" in their `supports:`
   text — never presented as current fact, only historical precedent (see
   ARCHITECTURE.md §5.1).
4. After any change under `knowledge/`, run `node scripts/validate-links.mjs`
   and confirm it exits 0 before considering the change done.
5. Adding a new component: add it to ARCHITECTURE.md §2's taxonomy table first,
   then create `knowledge/<group>/<id>.md`, add it to the group's `index.md`,
   add its `decision-matrix.yaml` entries, validate links.
6. Follow `IMPLEMENTATION_PLAN.md`'s phase order — don't start full data
   build-out (Phase 4) before the runtime is proven against the thin slice
   (Phase 3); don't build the Tier A/B/C pipeline (Phase 5) before the dataset
   it monitors exists (Phase 4).
7. Any new `chat/` tool holding conversation-scoped data uses `agent.state`
   via `ToolContext`, never a module-level variable — see "Chat runtime
   conventions" above.
