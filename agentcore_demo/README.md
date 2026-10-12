# Advisor Copilot — AgentCore demo

An agentic **Advisor Copilot** that helps a Financial Advisor prepare for a client
conversation: it looks up a client's portfolio, pulls market data, retrieves internal
research notes, and drafts compliant, grounded talking points. Built on **Amazon Bedrock
AgentCore** (Runtime, Gateway, Registry, Policy/Guardrails, Memory, Observability,
Evaluation) with **Strands Agents** as the agent framework.

All data is **synthetic** — no real client PII and no real market feeds. The agent
**advises**, it never executes trades.

See [`docs/design.md`](docs/design.md) for the
full architecture and the capability map.

## Repository layout

```
agent/   Strands agent + container (Runtime)   — config.py this phase
mocks/   Synthetic fixtures, Lambda handlers, OpenAPI schemas (Gateway targets)
bff/     FastAPI backend-for-UI
ui/      React + Vite single-page platform UI
infra/   AgentCore deploy scripts (boto3/CLI)   — later phases
scripts/ Operator helpers (preflight.sh)
```

## Phase 0/1 — run locally

Phase 0/1 is scaffold + synthetic mock data only. **No AWS calls are made.**

Requirements: Python 3.12 (pinned in `.python-version`; any `python3` runs the validators).
`node`/`npm` are **not** required for this phase — the UI is scaffolded as files only.

```bash
# 1. (optional) create a virtualenv and install the mock deps
python3 -m venv .venv && . .venv/bin/activate
pip install -r mocks/requirements.txt   # skip if network-restricted

# 2. validate the synthetic fixtures and OpenAPI schemas
python3 mocks/validate_fixtures.py
python3 mocks/validate_openapi.py

# 3. print the resolved config (local mode when infra IDs are blank)
python3 agent/config.py
```

`.env.example` lists every environment key. Copy it to `.env` and fill the infra IDs
(`GUARDRAIL_ID`, `GATEWAY_MCP_URL`, `RUNTIME_ENDPOINT_ARN`, `MEMORY_ID`) only after the
Phase 2 deploy; while they are blank the agent runs in **local mode**.

## Phase 3 — run the BFF + UI locally (end-to-end)

Phase 3 wires the FastAPI BFF and the React UI tabs. It is **local only** — the
only AWS traffic is the agent's own Bedrock model calls in `us-east-1` during a
chat turn. No AgentCore infra is created.

**Conventions (one choice, no second convention):**

- BFF listens on **port `8000`** (`BFF_PORT` in `.env.example`).
- UI reads the BFF base URL from **`VITE_BFF_BASE_URL`** (default
  `http://localhost:8000` — see `ui/src/api.ts`). Set it in `ui/.env` only if
  the BFF runs on a non-default host/port.

### 1. Agent — no separate process

There is **no separate agent process to start**. The BFF imports the Strands
agent **in-process**: `POST /api/chat` calls `run_agent` through the
`LocalChatBackend` seam (`bff/backends.py`), which invokes Bedrock directly.
Starting the BFF is all you need for chat to work.

### 2. Start the BFF

Run from the repo root so `agent` and `bff` are importable:

```bash
cd agentcore_demo
.venv/bin/python -m pip install -r bff/requirements.txt
AWS_REGION=us-east-1 .venv/bin/python -m uvicorn bff.main:app --port 8000
```

The BFF needs AWS credentials in the environment for the Bedrock model call
(`us-east-1`). All other endpoints work without credentials.

Smoke-test the chat SSE stream and the health banner:

```bash
curl -N -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"sessionId":"demo-1","message":"My client Jane Doe holds a concentrated position in NVDA and is nervous about rates. Summarize the latest research, check her portfolio exposure, and draft three talking points for our call."}'

curl -s localhost:8000/api/health
```

**REAL vs STUB endpoints (this phase):**

| Endpoint | Status |
|---|---|
| `POST /api/chat` | REAL — local agent (`run_agent`) → Bedrock, SSE relay |
| `GET /api/health` | REAL — truthful banner from `load_config()` |
| `GET /api/sessions`, `GET /api/sessions/{id}` | REAL — `agent.memory.SESSIONS` |
| `GET /api/traces`, `/api/traces/{id}` | STUB — Phase 6: CloudWatch GenAI Observability |
| `GET /api/guardrails` | STUB — Phase 6: Guardrail trace + logs |
| `GET /api/registry`, `POST /api/registry/{id}/approve` | STUB — Phase 6: bedrock-agentcore Registry |
| `GET /api/evaluations`, `POST /api/evaluations/run` | STUB — Phase 6: Evaluations + CloudWatch |

The chat backend sits behind a seam (`bff/backends.py`): `LocalChatBackend` now,
`RuntimeChatBackend` (deployed AgentCore Runtime) in Phase 5 — one factory line,
`/api/chat` contract unchanged.

### 3. Start the UI — DEFERRED until node/npm is installed

> **UI browser-run is DEFERRED: `node`/`npm` are not installed in this
> environment.** The UI source is complete and consistent with the BFF
> contract, but it was **not** type-checked, built, or browser-run here. Install
> node/npm, then run the exact commands below yourself.

```bash
cd ui
npm install           # installs react, react-router-dom, recharts, vite, tsc
npm run build         # tsc typecheck + vite build
npm run dev           # dev server on http://localhost:5173
```

Then open `http://localhost:5173`, pick the **Chat** tab, and send the demo
prompt (step 4). The health banner, Traces, Guardrails, Registry, Memory, and
Evaluations tabs all read from the BFF on `:8000`.

### 4. Demo prompt (Jane Doe / NVDA prep)

In the Chat tab (or the `/api/chat` curl above), send:

> My client Jane Doe holds a concentrated position in NVDA and is nervous about
> rates. Summarize the latest research, check her portfolio exposure, and draft
> three talking points for our call.

The agent streams tokens plus `tool_start`/`tool_stop` frames as it looks up the
portfolio, concentration flags, research notes, and the live NVDA quote, then
drafts grounded, compliant talking points (citing a research note id such as
`R-5567`).

## Eventual deploy flow (later phases)

The build is phased (see design.md §8):

- **Phase 0 — Scaffold.** Repo layout, synthetic fixtures, shared config, scripts. *(this phase)*
- **Phase 1 — Local-first agent.** Strands agent calling the mock data as local Python
  tools, Guardrail attached, BFF + UI running locally against Bedrock. Chat works end-to-end.
- **Phase 2 — AgentCore infra (us-east-1).** Stand up Guardrail → Gateway (Lambda targets)
  → Memory → Runtime (ARM64 container) → Registry → Observability + Evaluations, then
  switch the agent from local tools to Gateway MCP tools.
- **Phase 3 — Platform UI operator tabs.** Wire Traces, Guardrails, Registry, Memory,
  Evaluations to real AWS data via the BFF; add the health banner.
- **Phase 4 — Polish & demo script.** Seed the repeatable Jane Doe / NVDA demo plus
  guardrail-tripping and low-faithfulness prompts.

Before touching AWS, run the preflight check (requires valid credentials; **not** run in
Phase 0/1):

```bash
./scripts/preflight.sh
```
