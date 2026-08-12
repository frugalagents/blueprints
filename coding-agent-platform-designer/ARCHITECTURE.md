# Coding Agent Platform Designer — Architecture

Status: design doc, pre-scaffold. No code/data exists yet — this is the reference
for building it. Update this file if the design changes; it is the source of truth,
not the other way around.

## 0. Problem statement

A chat agent that helps someone design a fit-for-purpose coding agent platform —
architecture, tooling/integration, model/cost strategy, governance — end to end,
for a persona that isn't fixed yet (eng leader, individual team, or
security/platform reviewer may all use it).

Modeled on the sibling project `tokenomics/custom-agent-tokenomics` (content-as-data,
tiered source curation, drafts-for-review), but the content is *advisory/decision*
knowledge, not verifiable facts — so the architecture adds a decision layer and a
case-study source tier that tokenomics didn't need.

## 1. Three layers

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3 — RUNTIME (chat agent)                              │
│  Decision tree (narrows) + RAG over OKF graph (grounds)      │
│  + fallback to live proven sources when the wiki has a gap   │
├─────────────────────────────────────────────────────────────┤
│  LAYER 2 — DECISION MATRIX (structured, drives the tree)     │
│  org profile × component → applicable decisions, order       │
├─────────────────────────────────────────────────────────────┤
│  LAYER 1 — DATA (OKF knowledge base)                          │
│  24 components, one .md file each, linked into a graph       │
└─────────────────────────────────────────────────────────────┘
```

Each layer is collected/maintained differently, which is why they're kept separate:
Layer 1 is curated content (grows via the Tier A/B/C pipeline below), Layer 2 is
control logic (hand-authored, changes rarely), Layer 3 is the conversation loop
(code, not content).

## 2. Layer 1 — Data: the OKF knowledge base

### 2.1 Format: Open Knowledge Format (OKF v0.1)

- One markdown file per concept ("component"). File path = identity.
- YAML frontmatter for queryable fields; only `type` is required by the spec.
  We also use `title`, `description`, `group`, `tags`, `timestamp`, `status`.
- Markdown body is free-form. Relationships between components are **plain
  markdown links** in the body — no separate edges file. The directory of
  links is the graph.
- `index.md` per group folder for progressive disclosure; optional `log.md`
  for change history.
- Reference: <https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing>

Why OKF over the JSON-tab schema tokenomics uses: tokenomics content is lesson-like
(concepts/exercises/sources per tab); this content is graph-like (components that
reference and constrain each other) and benefits from links being the primary
structure rather than a flat list.

### 2.2 Baseline taxonomy from the source HTML (25 components, 8 groups)

Cross-checked directly against `blueprints/coding-agent-platform-arch.html`'s `M`
object on 2026-08-11:

| Group | Components |
|---|---|
| `surfaces` | `ide`, `cli`, `chat`, `ci` |
| `access` | `identity`, `guardrails`, `quota` |
| `registry` | `registry`, `tools`, `skills`, `subagents`, `mcpservers` |
| `harness` | `loop`, `perms`, `context`, `runtime` |
| `exec` | `exec` |
| `gateway` | `mcpgw`, `modelgw` |
| `external` | `landscape`, `web`, `providers` |
| `ops` | `observability`, `cost`, `token` |

4 + 3 + 5 + 4 + 1 + 2 + 3 + 3 = **25** (an earlier draft of this doc mis-summed
this as 24 — corrected here; the source HTML's card count is 25, verified by
direct enumeration, not arithmetic).

### 2.3 Gap audit and revised taxonomy (32 components, 9 groups)

The 25-component baseline was one hand-built diagram, not verified against how
real coding agent platforms are actually built. A gap audit (2026-08-12) against
Claude Code, Copilot/Workspace, Cursor, Devin, Q Developer, Replit Agent,
Windsurf, and AWS Bedrock AgentCore found 5 high-confidence gaps, adopted here,
plus 4 medium-confidence ones deferred to Tier B discovery (see below).

**Adopted (high confidence — real platforms treat these as architecturally
distinct, not options within an existing component):**

1. **`exec` is too coarse — split into 4 sandboxing components.** Isolation
   *mechanism* is a separate up-front decision from lifecycle/egress: AgentCore
   Runtime uses per-session Firecracker microVMs (hard kernel isolation, 8hr max
   lifetime); Devin/Replit Agent use remote ephemeral cloud VMs/workspaces;
   Cursor/Windsurf default to local process execution; pooled containers
   (Docker/gVisor) are a fourth, distinct blast-radius/cost tradeoff. These
   become `exec/local`, `exec/container`, `exec/microvm`, `exec/remote` —
   replacing the single `exec` component. Lifecycle and egress questions move
   into each, since they interact with the mechanism chosen.
2. **`harness/context` conflates task context with cross-session memory — split
   it.** AgentCore Memory itself formalizes this split: short-term session
   context (repo/file tree, current task) vs. long-term memory (persisted
   user/project facts across sessions) are separately configured, billed, and
   governed — the PII/residency implications of "remembers this user forever"
   differ sharply from "keeps the current file tree in context." `context`
   keeps the task-scoped meaning; new `harness/memory` takes the cross-session
   half.
3. **New: `harness/rollback` — version control / rollback safety for
   agent-authored changes.** Missing entirely from the baseline. Cursor's
   checkpoints, Devin's per-session branches, Copilot Workspace's PR-based
   isolation, and Claude Code's git-aware workflow all treat "cheaply undo/diff/
   branch many risky agent edits" as a core designed capability — about the code
   artifact, not the execution environment (`exec`'s "tear down clean" principle
   is lifecycle, not code safety).
4. **New: `registry/provenance` — supply-chain security for tools/skills/MCP
   servers.** Missing entirely from the baseline. OWASP's agentic security
   guidance and Claude Code's own plugin/MCP trust model both call out
   signing, provenance, and dependency-pinning as a pre-install-time vetting
   decision, orthogonal to `registry`'s cataloging and `guardrails`' runtime
   policy — an unsigned MCP server is an arbitrary-code-execution risk before
   any runtime policy ever runs.
5. **New group `quality`, component `evals` — agent evals / quality harness.**
   Missing entirely from the baseline; not covered by `ops/observability`
   (runtime tracing/audit, not build-time quality). Devin publishes SWE-bench-
   style regression suites; Claude Code and Copilot both gate model/prompt/tool
   changes on internal eval harnesses pre-deploy. This is about the *platform
   being designed* having its own eval gate — do not conflate with §8's item on
   evaluating this chat tool itself, which is a different concern entirely.

**Deferred to Tier B discovery (medium confidence — plausible but less settled
as of 2026, or ambiguous which group they belong to; corroborate via the
discovery-sweep pipeline before adopting as full components):**

- Multi-agent orchestration / A2A protocol as distinct from `registry/subagents`'
  "fixed roster" framing (real-world A2A adoption outside AgentCore/Google's
  ecosystem still nascent).
- Approval-workflow-as-a-queue (async, multi-approver, timeouts) distinct from
  `access/guardrails` + `harness/perms`.
- Browser/computer-use as its own surface (or an `exec` sub-pattern — genuinely
  ambiguous which; AgentCore ships Browser as a first-class tool alongside Code
  Interpreter).
- Data residency / VPC network placement as its own axis, not implied by
  `exec`'s egress question (AgentCore's `networkMode: PUBLIC | VPC` treats this
  as an explicit deployment decision).

**Revised taxonomy:**

| Group | Components |
|---|---|
| `surfaces` | `ide`, `cli`, `chat`, `ci` |
| `access` | `identity`, `guardrails`, `quota` |
| `registry` | `registry`, `tools`, `skills`, `subagents`, `mcpservers`, `provenance` |
| `harness` | `loop`, `perms`, `context`, `runtime`, `memory`, `rollback` |
| `exec` | `local`, `container`, `microvm`, `remote` |
| `gateway` | `mcpgw`, `modelgw` |
| `external` | `landscape`, `web`, `providers` |
| `ops` | `observability`, `cost`, `token` |
| `quality` | `evals` |

4 + 3 + 6 + 6 + 4 + 2 + 3 + 3 + 1 = **32** components, 9 groups.

If this list is revised again (component split, merged, renamed, or a deferred
item promoted out of Tier B), update this table and note the change in a
`log.md` — don't let the HTML source, this doc, and the wiki drift out of sync.
The source HTML (`coding-agent-platform-arch.html`) is the origin of the
baseline 25, not a live source of truth going forward — once `knowledge/` exists,
this doc and `knowledge/` are authoritative, and the HTML is historical context
only.

### 2.4 Directory layout

```
knowledge/
├── index.md
├── surfaces/   {index.md, ide.md, cli.md, chat.md, ci.md}
├── access/     {index.md, identity.md, guardrails.md, quota.md}
├── registry/   {index.md, registry.md, tools.md, skills.md, subagents.md, mcpservers.md, provenance.md}
├── harness/    {index.md, loop.md, perms.md, context.md, runtime.md, memory.md, rollback.md}
├── exec/       {index.md, local.md, container.md, microvm.md, remote.md}
├── gateway/    {index.md, mcpgw.md, modelgw.md}
├── external/   {index.md, landscape.md, web.md, providers.md}
├── ops/        {index.md, observability.md, cost.md, token.md}
└── quality/    {index.md, evals.md}
```

### 2.5 Per-component file schema

```markdown
---
type: platform-component
title: MCP Gateway
description: broker to tools & integrations
group: gateway
tags: [gateway, governance, tool-access]
timestamp: 2026-08-11T00:00:00Z
status: stable          # stable | candidate | deprecated
---

<1-2 paragraph summary of what this component is and why it exists.>

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

`## Decisions` stays plain markdown prose (not a fenced YAML block) — matches OKF's
"minimally opinionated" principle; the chat agent reads it via RAG, no parser
contract to keep in sync.

### 2.6 Edge vocabulary (convention, not enforced by OKF)

Keep the verb phrase in `## Connects to` consistent so the graph stays legible and
walkable by the runtime's 1-hop traversal:

| Phrase | Meaning | Example |
|---|---|---|
| "governed by / entitled by" | control-plane dependency | tools → identity |
| "routes through / brokers via" | mandatory chokepoint | landscape → mcpgw |
| "feeds / emits to" | telemetry flow | mcpgw → observability |
| "invoked by" | caller relationship | tools ← runtime |
| "loaded from" | registry/catalog sourcing | skills → registry |

### 2.7 Graph integrity

OKF does not enforce that links resolve. `scripts/validate-links.mjs` (new,
tokenomics has no equivalent) walks every `## Connects to` link in `knowledge/`
and fails if any target file doesn't exist — run in CI / before build, same spirit
as tokenomics' `build.mjs` exiting 0 as the done-condition.

## 3. Layer 2 — Decision matrix

Tokenomics has no equivalent: every learner sees every tab. Here, not every org
needs every component surfaced with equal weight, so this layer selects and orders.

### 3.1 Org-profile axes (asked once, up front)

| Axis | Values |
|---|---|
| Org size / team count | solo-team / multi-team / org-wide |
| Compliance posture | none / SOC2-type / regulated (HIPAA, FinServ, gov) |
| Existing stack | greenfield / has SSO+SCM already / has a competing agent tool live |
| Autonomy tolerance | advisory-only / gated-writes / full-auto-in-sandbox |
| Data sensitivity | public code / internal / regulated data in repos |

### 3.2 Matrix shape

`data/decision-matrix.yaml` — structured (YAML), not OKF, deliberately: this is
control logic the runtime evaluates deterministically, not knowledge content to RAG
over. Mixing the two would blur OKF's "format defines the interoperability surface,
not the content model" principle.

```yaml
components:
  identity:
    regulated: { relevance: required, priority: 1 }
    none:      { relevance: optional, priority: 3 }
  mcpgw:
    has-competing-tool: { relevance: required, priority: 1 }
    greenfield:         { relevance: optional, priority: 2 }
  # ... one entry per component per relevant axis value
```

### 3.3 Output

Given the 5 axis answers: an ordered, filtered list of the 32 components — e.g. a
regulated multi-team org front-loads `identity → guardrails → mcpgw →
observability → exec/microvm → provenance`; a solo greenfield team front-loads
`ide → loop → perms → exec/local` and may skip `quota`/`cost`/`provenance`
entirely (marked optional, not deleted — still answerable if asked).

### 3.4 Cross-component consistency checks

The matrix only selects and orders components — nothing yet checks that decisions
made across components don't contradict each other (e.g. picking "full auto" in
`harness/perms` earlier, then a `regulated` compliance posture implying
`access/guardrails` should have blocked that, but the two are never reconciled).

`data/decision-matrix.yaml` carries a `conflicts:` list alongside `components:`:

```yaml
conflicts:
  - if: { component: perms, option: full-auto }
    and: { component: guardrails, option: any-file-write-strict }
    warn: "Full-auto permission conflicts with strict write approval — pick one."
```

The runtime evaluates this against the running decisions log after every pick
(not just at the end) and surfaces the conflict inline, in the same turn — so the
user reconciles it while the context is fresh rather than discovering it in the
final `designs/<org-slug>.md` review.

## 4. Layer 3 — Runtime (chat agent)

```
User answers 5 profile-axis questions
        │
        ▼
Decision Matrix filters/orders the 32 components
        │
        ▼
For each component in order:
  - agent surfaces its "## Decisions" section conversationally
  - RAG retrieves the component's .md + 1-hop "Connects to" neighbors as grounding
  - user discusses/picks an option → recorded in a running decisions log
  - IF the question isn't answered by knowledge/ (component missing, or a decision
    option/edge case not covered) → FALLBACK (see 4.1)
        │
        ▼
Decisions log emitted as a new OKF file: designs/<org-slug>.md, linking back to
every component decided on — the output dogfoods the same format as the input.
```

### 4.1 Fallback: when the wiki doesn't have the answer

This is the gap between "curated knowledge base" and "agent must still answer
something reasonable." Two situations trigger it:

1. **Missing component** — the question is about something not yet in `knowledge/`
   at all (e.g. a brand-new sandbox-runtime category).
2. **Thin coverage** — the component file exists but doesn't address the specific
   question (a decision option, an edge case, a "does X integrate with Y" ask).

**Fallback mechanism:**

- `data/sources.json` carries a `fallback` pool: a curated allowlist of proven,
  vendor-neutral sites (official docs — AgentCore, Anthropic/Claude Code, MCP
  spec, OWASP agentic guidance — plus the major vendor docs: Copilot, Cursor,
  Devin). This reuses the Tier A monitoring list rather than duplicating it —
  every Tier A source is fallback-eligible by default; sources.json can mark any
  entry `fallbackEligible: false` if it's monitoring-only (e.g. too narrow to
  answer live questions).
- At query time, when RAG confidence is below threshold or no component matches,
  the runtime does a live `WebFetch`/search scoped to the `fallback` allowlist
  only (never open web) and answers from that, **explicitly labeled** to the user
  as "not yet in the curated wiki — sourced live from `<domain>`" so the user can
  tell curated-and-reviewed apart from live-and-unreviewed.
- Every fallback hit is logged to `data/state/gaps.json` (new file — append
  `{query, matchedComponent: null | id, timestamp}`). This is the feedback loop:
  gaps are exactly the seed queries the Tier B discovery sweep and Tier C
  case-study sweep should prioritize next, so real usage grows the wiki instead of
  the wiki only growing on a fixed cadence.
- Never silently answer from open-web/model-knowledge without hitting the
  allowlist and labeling the source — this is the same "no assertion without a
  citation" rule tokenomics enforces (its CLAUDE.md rule 2), extended to the
  runtime, not just the authoring pipeline.
- **Fetched content is untrusted input, even from the allowlist.** Allowlisted ≠
  safe to inject verbatim into the model's context — official docs pages can
  still carry injected instructions (the source HTML's own `external/web`
  component already names this principle: "filter fetched content for injection
  before use," which the original fallback design omitted). Every fallback fetch
  passes through the same content-filtering step before it reaches the model,
  no exception for allowlisted domains.

## 5. Collection & curation pipeline (three tiers)

| Tier | Purpose | Mechanism | Output |
|---|---|---|---|
| **A — Monitoring** | Keep `## Sources` citations current | `check-sources.mjs` hashes each Tier A URL's text, flags `changed` (adapted from tokenomics: hashes markdown body sections instead of JSON fields) | draft updates to the affected `.md` file's body/`## Sources` |
| **B — Discovery** | Find new components not yet in the 32 | `discovery-sweep` skill, corroboration threshold ≥2 independent sources (same as tokenomics) | new `knowledge/<group>/<id>.md` with `status: candidate`, tracked in `watchlist.json`; also consumes `gaps.json` as extra seed queries |
| **C — Case studies** (new vs. tokenomics) | Ground each `## Decisions` option in a real org's actual choice, not bare assertion | seeded queries against engineering blogs/conference talks about coding-agent platform rollouts | appended `## Sources` entries per option: "org X chose Y because Z — <link>" |

Governance rule carried over unchanged from tokenomics: automated output never
writes directly into `knowledge/`. It lands in `drafts/`; a human reads and
promotes it. This applies to Tier A, B, and C alike.

Tier B also runs the deferred medium-confidence gaps from §2.3 (A2A/orchestration,
approval-workflow, browser surface, data residency) through the standard
corroboration threshold (≥2 independent sources) before any of them is promoted
into the taxonomy as a real component.

### 5.1 Tier C staleness

Unlike Tier A (which has `check-sources.mjs` re-checking live docs on a cadence),
a Tier C case-study citation ("org X chose Y") is a point-in-time snapshot with no
equivalent freshness loop — the org's actual platform may have moved on since the
source was published. Two things follow: every Tier C `## Sources` entry must
carry an explicit "as of `<date>`" in its `supports:` text (never stated as
current fact, only as historical precedent), and `data/sources.json`'s Tier C
config carries a `reviewAfterMonths` per entry so stale case studies surface for
re-verification instead of aging silently forever.

## 6. Full file tree

```
blueprints/coding-agent-platform-designer/
├── ARCHITECTURE.md            — this file
├── CLAUDE.md                  — operational conventions (OKF rules, edge vocabulary, tier workflow)
├── knowledge/                 — OKF bundle (Layer 1)
├── data/
│   ├── decision-matrix.yaml   — Layer 2
│   ├── sources.json           — Tier A/B/C config + fallback allowlist
│   └── state/
│       ├── monitoring-state.json
│       ├── watchlist.json
│       └── gaps.json          — runtime fallback log, feeds Tier B/C seed queries
├── drafts/                    — pending Tier A/B/C output, human-reviewed before promotion
├── chat/                      — Layer 3 runtime: agent, matrix evaluator, OKF graph walker/RAG, fallback handler
├── scripts/
│   ├── check-sources.mjs
│   ├── manage-discovery.mjs
│   └── validate-links.mjs     — graph integrity check (new; OKF doesn't enforce this itself)
├── site/                      — optional: OKF reference visualizer pointed at knowledge/
└── ux/                        — optional: config/run management, same shape as tokenomics
```

## 7. Remaining design gaps (identified, not yet resolved)

Consistency checking (§3.4), Tier C staleness (§5.1), and fallback content
filtering (§4.1) are resolved above. Item 2 below is now built (2026-08-12);
items 1, 3, 4 are not yet designed:

1. **"I don't know" handling in the decision matrix.** §3.1's axis questions
   assume the user has an answer for each; real users often don't know their
   compliance posture or autonomy tolerance yet. Needs a defined behavior for
   unknown/skip — treat as the most conservative value (regulated-equivalent),
   not the most lenient, and let the profile refine mid-conversation as later
   answers narrow it.
2. **Output deliverable shape — resolved.** `chat/design_output.py`'s
   `generate_design_summary` tool renders an OKF-format markdown file
   (`designs/<org-slug>.md`): org profile, each decision **linked** to its
   source component in `knowledge/` (not restated — avoids a copy of
   component rationale that can drift from the source), a **Mermaid
   architecture diagram** (added 2026-08-12), and any conflicts flagged
   during the session. The diagram renders only the components this
   specific user decided on — not the full 32-component reference taxonomy —
   grouped by OKF group, each node labeled with the option chosen and status,
   edges drawn from real `## Connects to` links restricted to pairs where
   both ends were actually decided. When two components each independently
   declare a link to the other (describing one relationship from each side —
   e.g. identity/mcpgw), the generator collapses that into a single
   undirected edge rather than drawing two opposing arrows, which would
   visually read as a cycle that doesn't exist. Verified end-to-end against
   live Bedrock: multi-turn conversation → profile → decisions across
   multiple turns → `generate_design_summary` → a real file on disk with
   resolving links and a correctly-rendered diagram. Still minimal: plain
   Mermaid markdown, no exported image formats — revisit if that's needed
   once more users actually use it.
3. **No eval/quality loop on the chat agent itself.** `quality/evals` (§2.3) is
   about evaluating *the platform being designed* — this item is a distinct,
   separate concern: evaluating this chat tool's own advisory quality. Tokenomics
   doesn't need this (its facts are independently checkable); advisory synthesis
   is harder to verify — the underlying `.md` files can be correct while the
   agent's synthesis of them is still bad. Needs at minimum a small eval set of
   sample conversations with known-good component picks, to catch regressions.
4. **Session persistence.** Multi-turn design conversations likely span more
   than one sitting. Not yet defined whether/how an in-progress decisions log is
   saved and resumed across sessions.

## 8. Open items (not yet decided)

- Chat runtime framework — resolved: Strands Agents SDK on Bedrock
  (`us.anthropic.claude-sonnet-5`), per repo convention. See `chat/`.
- RAG retrieval mechanism for the 1-hop graph walk — resolved: naive
  markdown-link parsing (`chat/okf_graph.py`), sufficient at current scale.
- Whether `designs/<org-slug>.md` outputs are meant to be kept/shared or are
  session-scoped scratch — partially resolved: `designs/` is gitignored
  (2026-08-12), since it may contain sensitive org details (compliance
  posture, autonomy choices) and isn't the project's own curated content.
  Still open: any retention policy or review gate beyond that.
