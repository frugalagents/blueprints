---
type: platform-component
title: Execution — microVM Isolation
description: per-session hardware-virtualized sandbox
group: exec
tags: [exec, sandboxing, isolation]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

Runs each agent session inside its own micro-virtual-machine — a lightweight
VM with hardware-enforced CPU/memory/filesystem isolation, not just a
namespaced process. This is the strongest isolation tier short of a fully
separate physical host: two sessions on the same physical machine cannot see
or affect each other's memory, filesystem, or crashes, because each runs
under its own kernel.

This is one of four sandboxing components (see `exec/index.md` for the
others — `local`, `container`, `remote`) representing genuinely different
trust/cost tradeoffs, not options within a single "execution environment"
setting.

## Decisions

**When is microVM isolation worth it over a container?**
- Multi-tenant platform (agents from different users/orgs share physical
  infrastructure) — the hard isolation is the point
- Untrusted or partially-trusted code execution (agent-generated code you
  haven't reviewed) — commonly considered safer than shared-kernel container
  isolation for this case, since a microVM runs its own kernel; this
  comparison is not yet backed by a cited source here and should be verified
  against a container-specific source before being asserted as fact (see
  `## Sources`)
- Single-tenant, fully-trusted internal tooling — the extra isolation may not
  justify the cold-start and operational cost over a container

**Session lifecycle?**
- Ephemeral per session, fully torn down after — memory sanitized on
  teardown, no state carries over; matches "deterministic security even with
  non-deterministic AI processes"
- Persistent filesystem across stop/resume — files, installed packages, and
  build artifacts survive a session pause without needing external storage;
  useful for long-running or resumable tasks, but means teardown-and-sanitize
  no longer implies a fully clean slate

**Compute allocation model?**
- Fully managed, instant-start, scale-to-zero, pay-per-use — best default for
  bursty, many-short-sessions workloads
- Dedicated managed instances (own-account infrastructure) — needed for
  persistent multi-day sessions, GPU-accelerated workloads, or multiple
  agents collaborating on a shared instance

## Principles

- Isolate at the hardware-virtualization layer when sessions belong to
  different trust boundaries — don't rely on container namespacing alone for
  multi-tenant isolation
- Tear down and sanitize memory on session end unless there's a specific,
  named reason to persist state
- Cap session duration explicitly — long-running sessions need an
  extended-execution-time budget decision, not an unbounded one

## Connects to

- Invoked by the Tool Runtime (planned, `harness/runtime.md`)
- Governed by the same [Identity & Access](../access/identity.md) model for
  which sessions may run and under whose credentials

## Sources

- [Host agent or tools with Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.md) — checked 2026-08-12 — supports: per-session microVM isolation with dedicated CPU/memory/filesystem, teardown-and-memory-sanitize on session completion, choice between fully-managed microVMs and dedicated instances, up to 8-hour extended execution time, persistent filesystem across stop/resume cycles
