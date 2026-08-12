---
type: platform-component-group
title: Harness
description: the core agent loop and everything it directly depends on
group: harness
tags: [harness]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

The agent's core reason-act-observe cycle and its immediate dependencies:
what it's allowed to do, what it can see, how it invokes tools, and how its
edits can be undone. 6 components — 2 (`context`, `memory`) are a split of
what was originally one component; `rollback` is net new (see
`ARCHITECTURE.md` §2.3).

## Components

- Agent Loop — reason / act / observe cycle. Planned (Phase 4, batch 4).
- Permission Engine — allow / deny / ask per tool call. Planned (Phase 4, batch 4).
- Context — task-scoped repo/file context for the current session. Planned
  (Phase 4, batch 4).
- Memory — cross-session persisted facts, split from Context (see gap audit
  item 2). Planned (Phase 4, batch 4).
- Tool Runtime — invokes tools/subagents, dispatches to gateways. Planned
  (Phase 4, batch 4).
- [Rollback & Change Safety](rollback.md) — undo/diff/branch agent edits
  cheaply. Written (net new, gap audit item 3).
