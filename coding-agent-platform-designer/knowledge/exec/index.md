---
type: platform-component-group
title: Execution
description: where and how the agent runs code — 4 distinct sandboxing tiers
group: exec
tags: [exec, sandboxing]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

Where the agent's tool calls (shell, tests, builds) actually execute. Split
into 4 components representing genuinely different trust/blast-radius/cost
tradeoffs — not options within one setting (see `ARCHITECTURE.md` §2.3, gap
audit item 1, for why the original single `exec` component was too coarse).

## Components

- [microVM](microvm.md) — per-session hardware-virtualized isolation, the
  strongest tier short of a separate physical host. Written.
- Local — direct execution on the developer's machine. Planned (Phase 4, batch 5).
- Container — pooled Docker/gVisor-style shared-kernel isolation. Planned
  (Phase 4, batch 5).
- Remote — full remote ephemeral cloud VM/workspace. Planned (Phase 4, batch 5).
