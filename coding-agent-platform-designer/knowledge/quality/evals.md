---
type: platform-component
title: Agent Evals & Quality Harness
description: pre-deploy regression suites and quality gates for the platform being built
group: quality
tags: [quality, evals, regression]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

How the team building this coding agent platform knows a change to the
harness, prompt, model version, or tool set didn't regress quality — a
build-time, pre-deploy concern. This is distinct from Observability & Audit
(planned, `ops/`), which is about runtime tracing of live traffic, not
gating a change before it ships.

This is also distinct from a separate, unresolved concern noted in
`ARCHITECTURE.md` §7 item 3: evaluating *this advisory chat tool's own*
synthesis quality. That item is about the tool you're using right now to
design a platform; this component is about the platform being designed
having its own eval gate for the agent it ships. Don't conflate the two when
authoring content here.

This component did not exist in the platform's original architecture diagram
— added after a gap audit found no home for pre-deploy quality gating (see
`ARCHITECTURE.md` §2.3). Status is `candidate` pending Tier C corroboration.

## Decisions

**What does the eval suite run against?**
- A fixed set of golden tasks with known-good outcomes — cheap to run
  repeatedly, but only as good as the task set's coverage of real usage
- Real historical tasks sampled from production usage — better coverage,
  requires a pipeline to capture and label real tasks as they occur
- Both — golden tasks for fast regression signal, sampled real tasks for
  coverage; higher setup cost

**What blocks a release?**
- Any regression on the golden set — strict, may block on noise if the eval
  itself is flaky
- A regression threshold (e.g. pass rate drops more than N%) — tolerates some
  noise, requires deciding N deliberately rather than defaulting to zero
- Advisory only — evals report but don't block; fastest to ship, weakest
  guarantee

**Who reviews eval failures?**
- Automated gate only, no human review — fastest, risks shipping a subtle
  regression the eval didn't catch cleanly
- Human review required on any failure before merge/deploy — slower, but
  matches the same human-review-before-promotion posture this project's own
  data pipeline uses (see `ARCHITECTURE.md` §5)

## Principles

- An eval suite that never fails is not being run against hard enough tasks —
  treat a 100% pass rate with suspicion, not comfort
- Track pass rate over time, not just pass/fail on the latest run — a slow
  drift is a different signal than a sudden break
- Golden tasks should be updated when real usage reveals a gap, not frozen at
  creation time

## Connects to

- Gates changes to the Agent Loop (planned, `harness/loop.md`), tool set, and
  model routing before they reach real traffic
- Distinct from, and not a substitute for, Observability & Audit (planned,
  `ops/observability.md`) — evals run pre-deploy against known tasks;
  observability runs continuously against live, unknown traffic

## Sources

- Verify against current docs — no Tier A/C source captured yet for this
  component. Do not assert vendor-specific claims (e.g. "Devin publishes a
  SWE-bench-style suite") here until a real citation is on file; this file
  was authored from a gap audit's general observation that commercial coding
  agent platforms treat pre-deploy eval gating as a designed capability, not
  from a verified per-vendor source. Concrete candidate for the Tier C
  case-study sweep (see `ARCHITECTURE.md` §5).
