---
type: platform-component-group
title: Quality
description: pre-deploy eval gating for the platform being designed
group: quality
tags: [quality, evals]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

New group — did not exist in the original architecture diagram. Added because
no existing group covers build-time quality gating; `ops/observability`
covers runtime tracing of live traffic, a distinct concern (see
`ARCHITECTURE.md` §2.3, gap audit item 5).

## Components

- [Agent Evals & Quality Harness](evals.md) — pre-deploy regression suites and
  quality gates for the platform being built. Written.
