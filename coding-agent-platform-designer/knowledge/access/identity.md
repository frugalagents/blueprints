---
type: platform-component
title: Identity & Access
description: SSO · AuthN/Z · entitlements
group: access
tags: [access, governance, identity]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

Federates to your corporate identity provider and authenticates every request
before it reaches the harness — and it governs egress too: which identity may
reach which enterprise tool or dataset through the MCP Gateway. One access
model covers both ingress (who can talk to the agent) and tool egress (what
the agent can reach on their behalf).

Agent identities are commonly implemented as workload identities — a
specialized identity type distinct from human user identities, carrying
attributes (session scope, delegation chain) that let a platform tell "this
call is the agent acting for user X" apart from "this call is user X directly."

## Decisions

**Identity source?**
- Corporate IdP (Okta / Entra / Cognito) via OIDC/SAML — reuse existing
  enterprise auth, no new account system
- SCIM auto-provisioning of teams — access follows org-chart changes
  automatically, more setup cost up front (standard IdP capability, not
  yet verified against a coding-agent-specific source — see `## Sources`)

**Agent acts as whom?**
- The developer — inherits their access; simplest mental model, but the agent
  can now reach everything the developer can, even for read-only tasks
- Scoped service identity — least privilege; requires maintaining a separate
  entitlement mapping from developer to agent scope

**Tool-access model?**
- Role → allowed tools/datasets — coarse-grained, easy to reason about at scale
- Per-agent grants — explicit, but doesn't scale past a handful of agents
- Env-scoped — dev vs. prod tools differ; needed once agents can reach
  production systems at all

## Principles

- Federate — no local accounts to provision, rotate, or leak
- Same identity model governs ingress AND tool egress; don't split them into
  two systems that can drift out of sync
- Short-lived, scoped tokens — never long-lived static credentials for an
  agent identity

## Connects to

- Entitles every call through the [MCP Gateway](../gateway/mcpgw.md)
- Governed by the same identity source as (planned) Guardrails & Policy and
  Quota & Rate Limits — see `access/index.md`

## Sources

- [AgentCore Identity: Provide identity management for agent applications](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.md) — checked 2026-08-12 — supports: agent identities as workload identities distinct from human identities, native integration with an agent runtime and gateway for both inbound auth and outbound credential brokering
- [Manage credential providers with AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-outbound-credential-provider.md) — checked 2026-08-12 — supports: credential management across multiple trust domains as a distinct concern from inbound authentication
