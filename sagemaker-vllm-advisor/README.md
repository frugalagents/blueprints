# SageMaker vLLM Model Deployment Advisor

An evidence-first advisor with a React UI and a Python/FastAPI backend. It turns
model architecture and workload inputs into:

- GPU-memory and KV-cache calculations
- a SageMaker GPU instance shortlist (6 instance types, G6 through P5)
- initial vLLM server parameters
- a planning estimate for concurrency and replica count
- an inspectable Reason → Act → Observe trace
- explicit benchmark gates for unproven performance claims
- live Hugging Face model search and immutable revision metadata
- read-only AWS SageMaker quota and pricing evidence
- a Bedrock Converse tool-use agent
- immutable, non-executable deployment plans with cost and cleanup limits

Everything is deliberately read-only. The agent has no AWS write tools, and
deployment plans are non-executable by design.

## Architecture

| Layer | Location | Role |
| --- | --- | --- |
| Frontend | `src/` | React 19 + TypeScript single-page UI, built with Vite 7. `src/engine/` mirrors the backend sizing math for instant client-side previews; `src/services/` holds the API clients. |
| Backend | `backend/` | FastAPI app (`backend/main.py`) exposing the API below. |
| Sizing engine | `backend/calculators/sizing.py` | Deterministic memory / KV-cache / tensor-parallel / vLLM-settings math. |
| Domain models | `backend/domain/models.py` | Pydantic models; JSON API is camelCase, Python is snake_case. |
| Providers | `backend/providers/` | `huggingface.py` (model search + resolve) and `aws_readonly.py` (Service Quotas + Pricing, read-only). |
| Services | `backend/services/` | `advisor_service.py` (sizing + live AWS enrichment) and `deployment_plan.py` (hash-stamped, non-executable plans). |
| Agent | `backend/agent/` | `bedrock_agent.py` (Bedrock Converse tool-use loop) and `tools.py` (3 read-only tools). |

## API endpoints

The backend serves on `127.0.0.1:8000` locally.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Status, `awsWritesEnabled: false`, and the active Bedrock model id. |
| GET | `/api/models/search?q=` | Hugging Face text-generation model search. |
| GET | `/api/models/resolve?model_id=` | Resolve architecture + immutable revision metadata. `403` for gated models without `HF_TOKEN`. |
| POST | `/api/assessments/analyze?live_aws=true` | Sizing result. Falls back gracefully to offline sizing if live AWS calls fail. |
| POST | `/api/agent/ask` | Bedrock advisor (Converse API, tool-use loop). |
| POST | `/api/deployment-plans` | Immutable, non-executable deployment plan. |

## Prerequisites

- Node.js and npm
- Python 3.12
- AWS credentials on the standard boto3 credential chain (for live AWS evidence
  and the Bedrock agent). The app runs without them, degrading to offline
  planning previews.

## Run locally

### Option A — one command (macOS)

```bash
./start-app.command
```

This creates `.venv` if needed, installs backend and frontend dependencies,
starts the backend on `:8000` and the frontend on `:4173`, waits for both to
become healthy, and opens `http://127.0.0.1:4173`. Logs are written to
`.run/backend.log` and `.run/frontend.log`. Press Control-C to stop.

### Option B — manual

```bash
npm install
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

PYTHONPATH=. AWS_REGION=us-east-1 \
  .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
npm run dev
```

Then open `http://127.0.0.1:4173`. The Vite dev server proxies `/api` and
`/health` to the backend on port 8000, so the frontend uses same-origin
requests.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `AWS_REGION` | `us-east-1` | Region for the Bedrock runtime client and quota lookups. |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | Bedrock model used by the agent and reported by `/health`. |
| `HF_TOKEN` | _(unset)_ | Optional. Enables resolution of gated Hugging Face models. |
| `PYTHONPATH` | — | Must include the repo root (`PYTHONPATH=.`) because imports are `backend.*`. |

## Verify

```bash
npm test
npm run build
PYTHONPATH=. .venv/bin/pytest -q backend/tests
```

## Docker (backend only)

```bash
docker build -f backend/Dockerfile -t vllm-advisor-backend .
docker run -p 8080:8080 vllm-advisor-backend
```

The container serves on port `8080` (note: local dev uses `8000`).

## Safety boundary

The agent has no AWS write tools. Deployment plans are deliberately
non-executable until the exact plan hash is approved. Instance performance and
replica counts remain planning estimates until the approval-gated SageMaker
benchmark workflow is run.
