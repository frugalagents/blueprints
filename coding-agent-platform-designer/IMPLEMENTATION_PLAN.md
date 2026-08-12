# Coding Agent Platform Designer — Implementation Plan

Companion to `ARCHITECTURE.md` (the design). This is the *build order* — what
gets created, in what sequence, with what exit criteria before moving to the
next phase. Follow it top to bottom; don't skip a phase's exit criteria because
a later phase seems more interesting.

Each phase lists: **Goal**, **Deliverables**, **Exit criteria** (how we know
it's done), **Checkpoint** (what needs your review/decision before continuing).

---

## Phase 0 — Scaffold the shell

**Goal:** empty but correctly-shaped project, nothing load-bearing yet.

**Deliverables:**
- Directory tree per `ARCHITECTURE.md` §6, minus content: `knowledge/`,
  `data/state/`, `drafts/`, `chat/`, `scripts/`, empty placeholders only.
- `CLAUDE.md` for this project (operational conventions: OKF rules, edge
  vocabulary table, tier workflow — mirrors tokenomics' `CLAUDE.md` shape).
- `package.json` if any scripts need dependencies (likely minimal — OKF
  requires no SDK).

**Exit criteria:** directory tree matches ARCHITECTURE.md §6 exactly; `CLAUDE.md`
written.

**Checkpoint:** none — mechanical, proceed automatically.

---

## Phase 1 — Thin vertical slice (5 components)

**Goal:** prove the OKF schema and directory shape work before authoring all 32
components against it. Cheapest point to catch a schema mistake.

**Component selection** — 5 spanning different groups/layers so the slice
exercises the whole design, not just one corner:

| Component | Why this one |
|---|---|
| `access/identity` | control-plane dependency others link to |
| `exec/microvm` | newly-split component, sandboxing-specific fields |
| `gateway/mcpgw` | has rich `## Connects to` edges to test graph walk |
| `harness/rollback` | net-new component, no baseline HTML to transcribe from — tests authoring from scratch, not just transcription |
| `quality/evals` | new group — tests that group-level `index.md` works too |

**Deliverables:**
- 5 `.md` files per the schema in ARCHITECTURE.md §2.5, each with real
  `## Decisions`, `## Principles`, `## Connects to` (linking only within this
  slice — links to not-yet-written components get a `(planned)` note, not a
  broken link), and at least one real `## Sources` citation per file.
- `index.md` for each of the 4 touched groups (`access`, `exec`, `gateway`,
  `harness`, `quality`).
- `scripts/validate-links.mjs` — written now, not deferred, since this slice is
  exactly when a broken-link convention gets caught.

**Exit criteria:** `node scripts/validate-links.mjs` passes clean against the
5-file slice; each file independently readable (title, decisions, sources all
make sense to a human with no other context).

**Checkpoint:** review the 5 files together — this is where we catch if the
schema itself needs a field added/removed before it's copy-pasted 27 more times.

**Manual probe findings (2026-08-12, before Phase 2 started):** ran 3 by-hand
probes — simulating the RAG + graph-walk mechanic manually, no code — against
the 5-file slice to sanity-check the core mechanic before building on top of
it.

1. In-scope question (regulated fintech: exec + gateway + identity + rollback)
   → **worked well**. Correctly walked only the connected files, surfaced
   `candidate`-status caveats honestly instead of asserting them as fact.
   Validates the core RAG-over-OKF-links mechanic.
2. Out-of-scope question (model routing / token cost — components that don't
   exist yet) → **fails silently today**. There is no `data/sources.json`
   allowlist yet, so nothing constrains a fallback answer, nothing filters
   for injection, nothing labels the answer as uncurated, nothing logs to
   `gaps.json`. Without Phase 3's fallback handler, this failure mode is
   invisible to the user — it looks like a confident answer, not a gap.
3. Two conflicting asks in one question (auto-commit no-human-in-loop vs.
   every-edit-reviewed) → **only caught by manually re-reading
   `harness/rollback.md` closely**; nothing mechanized flagged it. Confirms
   Phase 2's `conflicts:` rule and Phase 3's inline conflict-check (§3.4) are
   load-bearing, not a nice-to-have — a real conversation can easily produce
   contradictory advice across two components without it.

Decision at the time: continue in the plan's original order (Phase 2 next,
Phase 3 after) rather than reordering to build the fallback/conflict-check
early. Carrying these findings forward so Phase 3's exit criteria (below) are
treated as closing a confirmed gap, not a hypothetical one — items 2 and 3 in
Phase 3's exit criteria are this probe's findings 2 and 3, restated as build
requirements.

---

## Phase 2 — Decision matrix + conflict rules (slice-scoped)

**Goal:** validate Layer 2 against the slice before writing all 32 components'
worth of matrix entries.

**Deliverables:**
- `data/decision-matrix.yaml` with `components:` entries for only the 5 slice
  components, across all 5 org-profile axes (ARCHITECTURE.md §3.1).
- At least one real `conflicts:` rule exercising §3.4 (e.g. a plausible tension
  between `identity` and `mcpgw` decisions, or fabricate one if the slice
  doesn't have a natural one — the point is proving the mechanism works).

**Exit criteria:** hand-trace 2 org profiles (e.g. "regulated multi-team" and
"solo greenfield") through the matrix on paper/in conversation and confirm the
filtered/ordered component list matches intuition.

**Checkpoint:** confirm the matrix output feels right for those 2 profiles
before building the runtime that consumes it.

---

## Phase 3 — Runtime, thin slice end-to-end

**Goal:** a working chat loop against the 5-component slice — this is the
riskiest unproven part of the whole design, so it runs before any more data
entry, not after.

**Deliverables:**
- `chat/` — agent implementation (framework choice resolved here, per
  ARCHITECTURE.md §8 open item — see decision note below).
- Matrix evaluator consuming Phase 2's YAML.
- OKF graph walker: parses `## Connects to` links, does the 1-hop retrieval
  described in ARCHITECTURE.md §4.
- Fallback handler (§4.1) wired to a small hardcoded allowlist (2-3 real URLs
  is enough to prove the mechanism — full `sources.json` config comes in
  Phase 5).
- Content-filtering step on fallback fetches (the injection-safety fix from
  §4.1) — not optional, build it now, not as a follow-up.
- `data/state/gaps.json` writer.

**Exit criteria** — run these 3 scripted conversations and confirm each works
as designed:
1. A question fully answerable from the 5-component slice → correct RAG +
   graph walk, no fallback triggered.
2. A question outside the slice (e.g. ask about a component not yet written,
   like `ops/cost`) → fallback triggers, answer is labeled
   "not yet in the curated wiki," gap logged to `gaps.json`.
3. A deliberate conflict per Phase 2's `conflicts:` rule → surfaced inline,
   same turn, not deferred to a final summary.

**Checkpoint:** this is the most important checkpoint in the whole plan — decide
whether the runtime shape (tree → RAG → fallback → conflict-check) actually
feels right in practice before scaling data entry 6x. If something about the
loop is wrong, it's cheap to fix now and expensive after Phase 4.

**Decision needed before this phase starts:** chat runtime framework. Per repo
convention (tokenomics uses Strands throughout) the default is Strands Agents
SDK on Bedrock — confirm or override before writing code.

**Status: done (2026-08-12).** Built `chat/okf_graph.py`, `chat/matrix.py`,
`chat/fallback.py`, `chat/agent.py` using Strands Agents SDK on Bedrock
(`us.anthropic.claude-sonnet-5`, verified live via
`aws bedrock list-inference-profiles` before use — do not assume a model ID
resolves without checking). All 3 scripted conversations passed against live
Bedrock, not just simulated. `data/sources.json` created with a minimal
3-URL fallback allowlist (full Tier A/B/C config remains a Phase 5
deliverable).

**Additional test round (2026-08-12, before moving to Phase 4):** ran 4 more
tests beyond the 3 exit-criteria conversations, per user request, to stress
the runtime harder before scaling data entry:

- **Multi-turn state persistence — found and fixed a real bug.** The first
  implementation kept the decisions log in a module-level Python global
  (`_decisions_log: list[Decision] = []`). A second `build_agent()` call
  (i.e. a second, unrelated conversation) shared the same list — decisions
  from one session were visible to, and would have silently commingled
  with, another. Root cause: no per-conversation scoping. Fix: moved the log
  into Strands' built-in per-agent `agent.state` (a JSON-serializable store
  scoped to the `Agent` instance), accessed via `@tool(context=True)` +
  `ToolContext.agent` on `record_decision`. Verified fixed: a second,
  independent `Agent` instance now correctly reports zero decisions instead
  of inheriting the first instance's state. Also added a `list_decisions`
  tool as a side effect — its absence was independently exposed by this same
  test (the agent had no way to answer "what have we decided so far?" without
  reconstructing from conversation memory, which rule 5 forbids).
- **Adversarial instruction-override attempt** — refused cleanly; the agent
  cited its own grounding rule as the reason rather than complying or
  silently ignoring the request.
- **Full org-profile walkthrough via natural conversation** (not calling the
  tool function directly) — `resolve_profile` invoked correctly from
  conversational input, correct required/priority ordering returned, and the
  agent disclosed `candidate` status before presenting details, unprompted.
- **Ambiguous multi-component question** (identity × mcpgw interaction) —
  correctly looked up both relevant components rather than stopping at the
  first match, and synthesized an answer across the `mcpgw` → `identity`
  graph edge with every claim traceable to actual file content. This is the
  first test to actually exercise cross-component synthesis via the graph
  link, not just single-component retrieval — validates the reason the OKF
  link structure exists at all.

Take-away carried into Phase 4: any future tool that holds conversation-scoped
data must use `agent.state`, never a module-level variable. This should be
called out in `CLAUDE.md` alongside the other rules so it isn't rediscovered
per-component.

---

## Phase 4 — Full data build-out (remaining 27 components)

**Goal:** author the rest of the taxonomy now that the schema and runtime are
proven against the slice.

**Approach:** batch by group, not all at once — each batch gets its own
`validate-links.mjs` pass before moving to the next, so broken cross-links are
caught incrementally rather than in one 27-file review at the end.

**Batches (7 remaining groups' worth, slice already covered access/exec/
gateway/harness-partial/quality):**
1. `surfaces` (`ide`, `cli`, `chat`, `ci`) — 4 files
2. `access` remainder (`guardrails`, `quota`) — 2 files
3. `registry` (all 6: `registry`, `tools`, `skills`, `subagents`, `mcpservers`,
   `provenance`) — 6 files
4. `harness` remainder (`loop`, `perms`, `context`, `runtime`, `memory`) — 5 files
5. `exec` remainder (`local`, `container`, `remote`) — 3 files
6. `gateway` remainder (`modelgw`) — 1 file
7. `external` (`landscape`, `web`, `providers`) — 3 files
8. `ops` (`observability`, `cost`, `token`) — 3 files

4+2+6+5+3+1+3+3 = 27, matching 32 total minus the 5-component slice.

**Deliverables per batch:** `.md` files + updated group `index.md` +
`validate-links.mjs` passing.

**Exit criteria (per batch):** links resolve; every `## Decisions` question has
≥2 options with a real tradeoff line (not a placeholder); every file has ≥1
real source.

**Exit criteria (whole phase):** all 32 components exist; full-taxonomy
`decision-matrix.yaml` complete (extend Phase 2's slice-scoped version to all
32); re-run the 3 Phase 3 conversation scripts against the full dataset to
confirm nothing regressed.

**Checkpoint:** spot-check 1 file per batch before moving to the next batch —
don't let a schema drift (e.g. someone's `## Connects to` phrasing not matching
the §2.6 vocabulary table) propagate across 27 files before it's caught.

---

## Phase 5 — Tier A/B/C pipeline

**Goal:** the curation/monitoring machinery, now that there's a full dataset
worth monitoring.

**Deliverables:**
- `data/sources.json` — real Tier A monitoring list, Tier B seed queries, Tier C
  case-study seeds + `reviewAfterMonths` (§5.1), fallback allowlist
  (`fallbackEligible` flags).
- `scripts/check-sources.mjs` (Tier A hash-diff, adapted for markdown bodies).
- `scripts/manage-discovery.mjs` (Tier B watchlist/drafts CLI).
- `discovery-sweep`-equivalent skill or process for Tier B + Tier C, including
  running the 4 deferred medium-confidence components (A2A, approval-workflow,
  browser surface, data residency — ARCHITECTURE.md §2.3) through the
  corroboration threshold.

**Exit criteria:** one real end-to-end run of each tier against the live
dataset, producing at least one real draft in `drafts/` (even if it's not
promoted).

**Checkpoint:** review the first batch of drafts before establishing this as a
recurring cadence — same human-review gate as tokenomics.

---

## Phase 6 — Close the remaining design gaps

**Goal:** resolve ARCHITECTURE.md §7's 4 remaining open items, now informed by
real usage from Phases 3-5 instead of speculation.

1. Unknown/skip handling in the matrix — informed by how often Phase 3's test
   conversations actually hit an "I don't know."
2. Output deliverable shape for `designs/<org-slug>.md` — informed by what the
   Phase 3 decisions log actually looked like in practice.
3. Eval set for the chat agent's own advisory quality — write the sample
   conversations now that real ones exist from Phases 3-5 to draw from.
4. Session persistence — design once it's clear from real usage how long a
   design conversation actually runs.

**Checkpoint:** each of these 4 is its own small decision — work through them
one at a time, not as a single batch.

---

## Phase 7 — Optional surfaces

**Goal:** only if wanted — not required for the tool to work.

- `site/` — OKF reference visualizer pointed at `knowledge/`.
- `ux/` — config/run management UI, same shape as tokenomics.
- `infra/` — deployment, same CDK pattern as tokenomics, if this needs to run
  anywhere other than locally.

**Checkpoint:** revisit whether this phase is even wanted once Phases 0-6 are
done — don't build it speculatively.

---

## Sequencing rules

- Do not start Phase 4 (full data build-out) until Phase 3's runtime is proven
  end-to-end. Data entry is the most expensive phase to redo; the schema and
  runtime are the cheapest to fix early and the most expensive to fix late.
- Do not start Phase 5 (pipeline automation) until Phase 4 is complete — there's
  nothing to monitor/discover against until the dataset exists.
- Phase 6 is deliberately *after* Phases 3-5, not before — every one of its 4
  items is better informed by real usage than by more up-front speculation.
- Phase 7 is explicitly optional and last.
