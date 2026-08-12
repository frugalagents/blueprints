---
type: platform-component
title: Rollback & Change Safety
description: undo, diff, and branch agent-authored edits cheaply
group: harness
tags: [harness, safety, version-control]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

How the platform lets an agent make many risky edits and cheaply undo, diff,
or branch them — a concern about the code artifact the agent produces, not
about the environment it executes in (that's [sandboxing](../exec/index.md)).
An agent that can write to a real working tree needs a defined recovery path
before a bad edit, a runaway loop, or a misread instruction becomes
expensive to reverse by hand.

This component did not exist in the platform's original architecture diagram
— it was added after a gap audit found no home for this concern anywhere in
the original 25-component taxonomy (see `ARCHITECTURE.md` §2.3). Status is
`candidate` until broader corroboration is captured under Tier C.

## Decisions

**Unit of reversibility?**
- Per-commit — every agent-authored change is its own commit, revertable
  individually; simple, but a bad multi-file change may span several commits
- Per-session checkpoint — snapshot the whole working tree at session
  boundaries; coarser, but matches "undo this entire agent run" as the
  common recovery need
- Per-file diff review before apply — the agent proposes, a human
  accepts/rejects per file before anything touches the real tree; strongest
  safety, slowest loop

**Isolation of agent edits from the main branch?**
- Direct commits to the working branch — fastest, no review step by
  construction; only safe when combined with a strict per-file review gate
- Dedicated branch per session — the agent's changes are isolated until a
  human merges; cheap to discard an entire bad session
- PR-based — agent opens a pull request instead of committing directly;
  isolation plus the review step happen through existing team workflow rather
  than a bespoke mechanism

**What triggers an automatic rollback vs. a human decision to roll back?**
- Never automatic — a human always decides; safest, but doesn't help with
  runaway loops that fail fast
- Automatic on failed verification (tests fail, build breaks) — catches the
  common case without waiting on a human
- Automatic on budget/step-cap exceeded — ties into the Agent Loop's (planned,
  `harness/loop.md`) autonomy-per-task decision

## Principles

- Never let "hard to undo" be the reason an edit gets approved — reversibility
  should be cheap enough that it's not a factor in the approval decision
- Isolate agent edits from the branch a human is actively working on, by
  default — direct-to-shared-branch commits should be an explicit choice, not
  the starting posture
- Every rollback action is itself audited — same principle as tool calls

## Connects to

- Constrained by the autonomy-per-task decision in the Agent Loop (planned,
  `harness/loop.md`)
- Executes within whichever [sandboxing component](../exec/microvm.md) is
  chosen — rollback safety and execution isolation are complementary, not
  substitutes for each other

## Sources

- Verify against current docs — no Tier A/C source captured yet for this
  component. Do not assert vendor-specific claims (e.g. "Cursor does X",
  "Devin does Y") here until a real citation is on file; this file was
  authored from a gap audit's general observation that commercial platforms
  treat this as a designed capability, not from a verified per-vendor source.
  This is a concrete candidate for the Tier C case-study sweep (see
  `ARCHITECTURE.md` §5).
