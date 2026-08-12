---
type: platform-component
title: MCP Gateway
description: broker to tools & integrations
group: gateway
tags: [gateway, governance, tool-access]
timestamp: 2026-08-12T00:00:00Z
status: candidate
---

The single chokepoint for all tool/MCP traffic to enterprise systems and
web/search. The harness asks the gateway; the gateway verifies the tool is
approved and the caller entitled, applies policy and rate limits, injects
credentials, and audits every call. Concretely, a gateway is itself a managed
service exposing a heterogeneous set of backends — Lambda functions, REST
APIs, OpenAPI/Smithy-described services, other agents, remote MCP servers,
even other model providers — as one unified endpoint the agent discovers and
calls, rather than the harness integrating with each backend directly.

## Decisions

**Deployment?**
- Central shared gateway — one policy point, simpler ops, and a single place
  to apply rate limits/kill-switches across every integration
- Per-tenant — stronger isolation between tenants, more infrastructure to run
  and keep in sync

**Inbound authorization?**
- No authorizer — fine for early development, not for anything reachable by
  more than the builder
- IAM-based — fits when callers are already AWS-principal-based
- Custom JWT (via your corporate IdP's discovery URL) — fits when callers are
  end users or agents authenticated through the same IdP as everything else
  (ties back to [Identity & Access](../access/identity.md))

**Credential handling for outbound calls to backends?**
- Broker injects at call-time from a separately-configured credential
  provider — secrets (API keys, OAuth client secrets) never enter the agent's
  context or get passed as tool-call parameters
- Short-lived, scoped tokens per session/target rather than long-lived static
  credentials

**Tool discovery at scale?**
- Flat list — fine for a handful of tools
- Semantic search over the tool catalog — needed once the number of exposed
  tools grows large enough that an agent can't usefully enumerate all of them
  in context; also reduces prompt size and latency at scale

## Principles

- All tool traffic proxied through the gateway — no direct connections from
  harness to backend systems
- AuthZ + scope check + audit on every call, not just at connection setup
- Rate-limit and kill-switch per integration, so one misbehaving backend or
  compromised tool can be cut off without taking down the whole gateway
- Credential material is never accepted as a raw parameter into a tool call —
  it is referenced by provider ID and resolved server-side

## Connects to

- Enforces entitlements set by [Identity & Access](../access/identity.md)
- Brokers calls into Enterprise Landscape and Web & Search (planned,
  `external/`)
- Feeds call traces to Observability & Audit (planned, `ops/`)
- Invoked by the Tool Runtime (planned, `harness/`), never called directly by
  developer-facing surfaces

## Sources

- [Amazon Bedrock AgentCore Gateway: A secure AI gateway for agents, tools, and models](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.md) — checked 2026-08-12 — supports: gateway as a single managed endpoint unifying heterogeneous backend types (APIs, Lambda, other agents via A2A passthrough, remote MCP servers, model providers) with both comprehensive ingress and egress authentication, semantic tool search at scale, and server-side credential exchange never passed as inline parameters
- AgentCore Gateway implementation guide (`get_gateway_guide`, fetched live via the bedrock-agentcore MCP server, no stable public URL captured — flag for a follow-up Tier A source once a docs URL is confirmed) — checked 2026-08-12 — supports: specific inbound authorizer types (NONE, AWS_IAM, CUSTOM_JWT with IdP discovery URL), credential providers never accepting raw secrets as tool-call parameters, semantic search reducing prompt size/latency at scale
