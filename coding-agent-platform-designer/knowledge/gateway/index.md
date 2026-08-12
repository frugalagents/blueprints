---
type: platform-component-group
title: Gateway
description: broker layer between the harness and everything outside it
group: gateway
tags: [gateway, governance]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

The chokepoints for outbound traffic from the harness — tools/integrations
and model providers each get their own gateway, so policy, credential
brokering, and audit apply uniformly regardless of which backend is called.

## Components

- [MCP Gateway](mcpgw.md) — broker to tools & integrations. Written.
- Model Gateway — broker to model providers. Planned (Phase 4, batch 6).
