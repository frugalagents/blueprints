# Design & Architecture — Advisor Copilot (AgentCore demo)

> Status: DRAFT for review · Region: `us-east-1` · Framework: Strands Agents · Platform: Amazon Bedrock AgentCore (GA, Oct 2025)

---

## 1. Purpose & Use Case

### 1.1 Business context
A wealth-management business runs on thousands of Financial Advisors (FAs) who prepare for client meetings. Preparation is manual: pull the client's portfolio, read the latest sell-side research, check market moves, and assemble talking points — all while staying inside strict compliance guardrails (no guaranteed-return language, mandatory disclaimers, no leaking of other clients' data).

### 1.2 The POC
**Advisor Copilot** — an agentic application a Financial Advisor uses to prepare for a client conversation.

Representative prompt:
> "My client Jane Doe holds a concentrated position in NVDA and is nervous about rates. Summarize the latest research, check her portfolio exposure, and draft three talking points for our call."

The agent:
1. Looks up the client's portfolio and positions (mocked Portfolio API).
2. Pulls recent market data for the relevant holdings (mocked Market Data API).
3. Retrieves the latest internal research notes (mocked Research API).
4. Synthesizes grounded, compliant talking points, applying guardrails and disclaimers.

### 1.3 Why this use case
It is realistic for MS, compliance-forward (which is MS's reality as a regulated broker-dealer), and every AgentCore primitive has an honest job rather than being bolted on. The agent **advises**, it does not **execute trades** — keeping the POC credible and safe to demo.

### 1.4 Explicit non-goals
- No real trade execution, order routing, or money movement.
- No connection to real MS systems or licensed market-data feeds (all data mocked).
- No real client PII — synthetic clients only.
- Not a production security review; POC-grade IAM with documented hardening notes.

---

## 2. AgentCore Capability Map

Each capability must earn its place. This table is the contract for the whole POC.

| # | AgentCore capability | Role in this POC | Demo-visible artifact |
|---|---|---|---|
| 1 | **Runtime** | Hosts the Strands agent as an ARM64 container. Protocol: **HTTP** (`/invocations` + `/ping`) with SSE streaming — simplest contract, works cleanly behind the FastAPI BFF. | Chat responses stream into the UI. |
| 2 | **Gateway** | Converts the three mocked APIs (Portfolio, Market Data, Research) into MCP tools the agent calls. Targets backed by **Lambda with an inline tool schema** (derived from our OpenAPI operations) — the current AgentCore pattern; no S3 schema bucket. Inbound auth **AWS_IAM / SigV4** for the POC (CUSTOM_JWT is the production path). | "Tools" panel lists MCP tools generated from each target. |
| 3 | **Registry** | Catalogs the agent (A2A card) and its MCP tools so they are discoverable and governed. Manual-approval governance to show the compliance workflow. | "Registry" tab: records with `DRAFT → PENDING_APPROVAL → APPROVED` states. |
| 4 | **Policy / Guardrails** | Bedrock Guardrail: block PII leakage, deny prohibited advice ("guaranteed returns", "can't lose"), enforce topic boundaries, inject disclaimers. | "Guardrails" tab: intervention events with the triggering rule. |
| 5 | **Memory** | Per-session memory so the FA can have a multi-turn conversation about the same client without restating context. Short-term (conversation) + optional long-term (client preferences). | "Memory" tab: active sessions and stored events. |
| 6 | **Observability** | ADOT instrumentation → CloudWatch GenAI Observability. Traces for every tool call, latency per step, token usage. | "Traces" tab: span waterfall per invocation. |
| 7 | **Evaluation** | LLM-as-a-judge scoring: `Faithfulness` (advice grounded in retrieved research), `Harmfulness`, `Correctness`, `ToolSelectionAccuracy`. Online sampling + on-demand batch over a seed dataset. | "Evaluations" tab: scores over time + per-session drill-down. |

---

## 3. System Architecture

### 3.1 High-level diagram

```
                         ┌─────────────────────────────────────────┐
                         │        Platform UI (React + Vite)        │
                         │  Chat │ Traces │ Guardrails │ Registry   │
                         │        │ Memory │ Evaluations            │
                         └───────────────────┬─────────────────────┘
                                             │ HTTPS (REST + SSE)
                         ┌───────────────────▼─────────────────────┐
                         │        FastAPI BFF (Backend-for-UI)       │
                         │  - proxy chat → AgentCore Runtime (SSE)   │
                         │  - read CloudWatch traces / metrics       │
                         │  - read Guardrail events                  │
                         │  - read Registry records                  │
                         │  - read Memory sessions                   │
                         │  - read Evaluation scores                 │
                         └──────┬───────────────────────┬───────────┘
                                │ invoke                 │ read (boto3)
          ┌─────────────────────▼──────────┐   ┌─────────▼───────────────────────┐
          │   AgentCore Runtime            │   │ CloudWatch (traces, metrics,     │
          │   Strands agent (ARM64, HTTP)  │   │ GenAI Observability), Evaluations │
          │   + ADOT instrumentation       │   │ scores, Registry, Memory store    │
          │   + Guardrail applied           │   └──────────────────────────────────┘
          └───┬───────────────┬────────────┘
              │ MCP            │ Memory API
     ┌────────▼─────────┐  ┌──▼───────────────┐
     │ AgentCore Gateway │  │ AgentCore Memory │
     │  3 MCP targets    │  │ (session store)  │
     └────────┬─────────┘  └──────────────────┘
              │ OpenAPI → Lambda
   ┌──────────▼───────────────────────────────┐
   │  Mock data Lambdas (synthetic)            │
   │  • Portfolio API  • Market Data API       │
   │  • Research Notes API                     │
   └───────────────────────────────────────────┘
```

### 3.2 Component responsibilities

**Strands agent (Runtime).** A single Strands `Agent` with a system prompt tuned for an FA assistant. Tools are the MCP tools exposed by Gateway. Model: Claude (Bedrock) — see §6. Instrumented with ADOT so every tool call and model call emits spans. Guardrail ID attached at invocation.

**Gateway + mock Lambdas.** One MCP Gateway (`authorizerType=AWS_IAM`) fronts three Lambda targets. Each target uses an inline `toolSchema` (tool definitions derived from the OpenAPI operations) pointing at a Lambda that returns deterministic synthetic data (seeded fixtures), so the demo is repeatable. No S3 schema bucket is used (the inline-toolSchema pattern supersedes the older OpenAPI-in-S3 approach). Live resources: gateway `advisor-copilot-gateway`, 3 targets READY, 5 generated tools (getClientPortfolio, getMarketQuote, getMarketQuotes, listResearchByTicker, getResearchNote).

**Memory.** Runtime passes `sessionId`; Memory persists conversation turns. The UI's Memory tab reads back events for the active session.

**Guardrails.** One Bedrock Guardrail versioned and attached. Denied-topic + word policies for prohibited advice; PII filters; a contextual-grounding check to reduce hallucinated figures.

**Observability + Evaluation.** ADOT exports to CloudWatch GenAI Observability. Evaluation uses the emitted traces: online evaluation at ~10% sampling plus an on-demand batch over a curated seed set of advisor prompts.

**FastAPI BFF.** The only component the UI talks to. It (a) proxies chat to the Runtime endpoint and relays the SSE stream, and (b) exposes read-only operator endpoints that call boto3 (`bedrock-agentcore`, `cloudwatch`, `logs`) and shape data for the UI. Keeps AWS credentials server-side.

**Platform UI.** React + Vite SPA, one app with tabbed views (§5).

---

## 4. Data Model (mocked, synthetic)

### 4.1 Client / Portfolio
```json
{
  "client_id": "C-1001",
  "name": "Jane Doe",
  "risk_profile": "moderate",
  "advisor_id": "FA-77",
  "positions": [
    {"ticker": "NVDA", "shares": 4200, "cost_basis": 310.5, "sector": "Technology"},
    {"ticker": "AGG",  "shares": 1500, "cost_basis": 98.2,  "sector": "Fixed Income"}
  ],
  "concentration_flags": ["NVDA 38% of equity sleeve"]
}
```

### 4.2 Market data (snapshot, synthetic)
```json
{"ticker": "NVDA", "last": 184.2, "change_pct": -2.1, "pe": 48.3, "as_of": "2026-10-10T15:00:00Z"}
```

### 4.3 Research note
```json
{
  "note_id": "R-5567", "ticker": "NVDA", "analyst": "Internal Research",
  "rating": "Overweight", "price_target": 210,
  "published": "2026-10-08",
  "summary": "Datacenter demand remains strong; rate sensitivity moderate...",
  "disclaimer_required": true
}
```

### 4.4 Seed data volume
~8 synthetic clients, ~15 tickers, ~20 research notes. Deterministic fixtures checked into the repo so demos are reproducible and offline-capable for the local phase.

---

## 5. Platform UI

Single SPA, left-nav tabs. Design goal: one screen that proves every capability is live.

| Tab | Shows | Data source (via BFF) |
|---|---|---|
| **Chat** | The FA experience. Streaming responses, tool-call chips inline, session selector. | Runtime (SSE proxy) |
| **Traces** | Span waterfall per invocation: model calls, each tool call, latency, token counts. | CloudWatch GenAI Observability |
| **Guardrails** | Table of intervention events: timestamp, rule type (denied topic / PII / word), masked snippet, action taken. | Guardrail trace + logs |
| **Registry** | Catalog records (agent + tools), governance state, approve/reject action. | `bedrock-agentcore` Registry |
| **Memory** | Active sessions, event list for a session, TTL. | AgentCore Memory |
| **Evaluations** | Score cards (Faithfulness, Harmfulness, Correctness, ToolSelection) trended; per-session drill-down; "run batch eval" button. | Evaluations + CloudWatch |

A top banner shows live health: Runtime status, Gateway target status, Guardrail version, model ID — so a demo viewer sees the whole platform is wired.

---

## 6. Model Selection

- **Primary agent model:** Claude (Anthropic) on Bedrock — strong tool use and instruction following, well-suited to grounded financial summarization. Exact model ID confirmed against the model-selection reference at build time (prefer a current Claude Sonnet class model in `us-east-1`).
- **Evaluation judge model:** a strong Bedrock model distinct enough to act as an impartial judge; configured in the Evaluations service.
- Rationale and the exact pinned model IDs will be recorded in the build phase after confirming availability in `us-east-1`.

---

## 7. API Contracts (BFF)

```
POST /api/chat            # body {sessionId, message} → SSE stream of tokens + tool events
GET  /api/sessions        # list memory sessions
GET  /api/sessions/{id}   # memory events for a session
GET  /api/traces?limit=   # recent invocation traces (span summaries)
GET  /api/traces/{id}     # full span waterfall
GET  /api/guardrails      # intervention events
GET  /api/registry        # catalog records + governance state
POST /api/registry/{id}/approve   # approve a pending record
GET  /api/evaluations     # score summary + trend
POST /api/evaluations/run # trigger on-demand batch eval over seed set
GET  /api/health          # runtime/gateway/guardrail/model status banner data
```

All operator endpoints are read-only except the two explicit POSTs (registry approve, run eval), which mirror real governance actions.

---

## 8. Build Plan & Phasing

### Phase 0 — Scaffold
Repo layout, synthetic fixtures, shared config, Makefile/scripts.

```
agentcore_demo/
├── agent/            # Strands agent + container (ARM64)
│   ├── app.py        # HTTP server: /ping, /invocations (SSE)
│   ├── agent.py      # Strands Agent definition + tools
│   ├── Dockerfile
│   └── requirements.txt
├── mocks/            # synthetic data + Lambda handlers + OpenAPI schemas
│   ├── fixtures/     # clients.json, market.json, research.json
│   ├── portfolio_lambda.py
│   ├── market_lambda.py
│   ├── research_lambda.py
│   └── openapi/      # portfolio.yaml, market.yaml, research.yaml
├── bff/              # FastAPI backend-for-UI
│   ├── main.py
│   └── requirements.txt
├── ui/               # React + Vite SPA
│   └── src/ ...
├── infra/            # deploy scripts for AgentCore (boto3/CLI)
│   ├── 01_guardrail.py
│   ├── 02_gateway.py
│   ├── 03_memory.py
│   ├── 04_runtime.py
│   ├── 05_registry.py
│   └── 06_evaluations.py
└── docs/design.md   # this doc
```

### Phase 1 — Local-first agent
Build the Strands agent calling the mock data **directly as local Python tools** (no Gateway yet). Attach a Bedrock Guardrail. Run the FastAPI BFF + UI locally against Bedrock. Goal: the Chat tab works end-to-end locally. **This de-risks everything before touching AgentCore infra.**

### Phase 2 — AgentCore infra (us-east-1)
Stand up, in order:
1. **Guardrail** (Bedrock) — create + version.
2. **Mock Lambdas + Gateway targets** — Portfolio, Market, Research as MCP tools.
3. **Memory** — session store.
4. **Runtime** — build ARM64 container, push to ECR, create runtime + endpoint, attach guardrail + memory, ADOT instrumentation.
5. **Registry** — register agent + tools, manual-approval governance.
6. **Observability + Evaluations** — confirm traces land; create evaluators; configure online sampling + seed batch.

Switch the agent from local tools to Gateway MCP tools.

### Phase 3 — Platform UI operator tabs
Wire Traces, Guardrails, Registry, Memory, Evaluations tabs to real AWS data via BFF. Health banner.

### Phase 4 — Polish & demo script
Seed a repeatable demo flow (the Jane Doe / NVDA prompt), a guardrail-tripping prompt, and a low-faithfulness prompt to show evaluation catching it.

---

## 9. Security & Compliance Notes (POC-grade, with hardening callouts)

- IAM: least-privilege roles per component; documented, not production-audited.
- No real PII; synthetic clients only.
- Guardrails enforce prohibited-advice and PII policies — core compliance story.
- Secrets (if any mock API keys) via Secrets Manager, never in env vars or code.
- CloudWatch log groups: KMS encryption + retention noted as a hardening step.
- Runtime authorizer: POC may use a simple inbound auth; production would require Cognito/OIDC JWT (documented).
- Registry: manual-approval governance to mirror a real control.

---

## 10. Open Questions for Review

1. **Agent model** — OK to default to a current Claude Sonnet-class model on Bedrock in `us-east-1`, pinned at build time? (vs. Nova or another family.)
2. **Local-first (Phase 1) before AgentCore deploy (Phase 2)** — agree with that order? It de-risks the agent logic before infra.
3. **Runtime protocol** — HTTP+SSE is my recommendation (simplest, streams to UI cleanly). Alternative is AG-UI. OK with HTTP?
4. **Depth of Registry/Evaluations** — these are the newest surfaces. OK to keep them demo-depth (register the records, run a seed batch + light online sampling) rather than exhaustive?
5. **Auth on Runtime** — fine to keep POC inbound auth simple and document the production JWT path, or do you want Cognito wired now?
6. **One agent vs. multi-agent** — single agent with multiple tools is my recommendation for clarity. Do you want a second "compliance reviewer" sub-agent to showcase A2A/multi-agent? (Adds scope but is a strong MS story.)
```
