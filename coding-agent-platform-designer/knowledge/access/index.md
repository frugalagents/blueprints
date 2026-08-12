---
type: platform-component-group
title: Access
description: control plane — identity, policy, quota
group: access
tags: [access, governance]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

The governance control plane: who may reach the harness, what they may do
once they're in, and how much of it they may do. Every other group's
components answer to this one for entitlement decisions.

## Components

- [Identity & Access](identity.md) — SSO / AuthN/Z / entitlements. Written.
- Guardrails & Policy — filtering, approvals, DLP. Planned (Phase 4, batch 2).
- Quota & Rate Limits — throttling, seats, spend caps. Planned (Phase 4, batch 2).
