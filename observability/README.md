# Consolidated agent observability — local quick start

Run a synthetic refund workflow across three agent frameworks and inspect their
OTel in one local UI. No real payment is issued.

```text
Start workflow → A: Strands → B: Agents SDK → C: LangChain policy auditor
                         ← verified eligibility ← verified audit

A OTel ────────────────────────────────┐
B native SDK → OpenInference → OTel ──┼→ OTLP → Collector → SQLite → custom UI
C LangChain → OpenInference → OTel ───┘
```

The same trace context crosses both HTTP handoffs. Each agent has a separate run.
The UI compares native records, source OTel and Collector-processed OTel.
No Phoenix, AgentCore Observability or CloudWatch backend is required.

## Start (any macOS/Linux machine)

```sh
python3 start.py
```

On first run this installs the isolated environments and the Collector (needs
Python 3.14 on PATH), then starts all services and opens Connected in your
browser once the Collector is ready. Press **Ctrl+C** to stop. Your own AWS SDK
credentials must still be valid. On macOS you can also double-click
**Start Observability.command**. If ports are busy, stop the previous demo first.

## Prerequisites

- Git and Python **3.14** with `venv`/`pip`.
- macOS or Linux, ARM64 or x86_64. Windows users can try Linux under WSL2;
  native Windows is not supported by these scripts.
- Network access to the Python package index, official Collector GitHub releases
  and Amazon Bedrock.
- Your own AWS credentials configured for the Python SDK, and permission to use
  Bedrock Converse with the demo's inference profile.
- The current model configuration is **`us.anthropic.claude-sonnet-5-5` in
  `us-east-1`**. Confirm your account can invoke it. Setup does not grant access
  or silently substitute a different model.
- Free local ports: **8775** (UI), **8786** (C), **4318** (OTLP), **13133**
  (Collector health).

## Install and start

Replace `<repository-url>` with the Git repository you publish:

```sh
git clone <repository-url> agent-observability
cd agent-observability
python3.14 scripts/setup.py --plan
python3.14 scripts/setup.py
python3.14 scripts/run.py
```

Setup creates three isolated Python environments, installs the requirements, runs
`pip check`, downloads Collector Contrib **0.162.0** for your OS/architecture,
verifies its archive against the release's SHA256 manifest, and creates capture
folders. A pins direct dependencies; B/C have captured version lock files.

If you use a named AWS SDK profile, set it before starting:

```sh
export AWS_PROFILE=your-profile
python3.14 scripts/run.py
```

Credentials stay in your own AWS configuration; never commit them.
Model selection is currently in `app/agent1/refund_agent.py`,
`app/agent2/eligibility_agent.py` and `app/agent3/native_agent.py`
(C imports the latter's constants). There is no shared model override yet.

Open **http://127.0.0.1:8775/connected.html**. Pick **Eligible → refund** and click
**Start workflow**. The right panel updates automatically as completed spans arrive.
Bedrock calls incur inference charges.

Press **Ctrl+C** in the launcher terminal to stop the app, C and Collector.

## Check that it worked

1. A, B and C should each show captured spans in Connected.
2. The tree should show one trace and recorded A→B / B→C parent boundaries.
3. The collection status should finish with **preservation passed**.
4. The synthetic eligible outcome should be **refunded**, with verified DB state.
5. Try **Denied → no refund**; verification should pass with zero refund rows.

Span counts can vary with model behavior. `UNSET` is a span's default status,
not a running indicator or proof of business success.

The source tabs inspect individual agents. Connected starts and inspects the whole
business execution. Pipeline provides generic service discovery and processing
evidence. A fresh installation has no saved workflows: generate your own.

## What is stored locally

- Agent/native audit captures: `data/captures/`.
- Processed spans and retained OTLP batches: `data/backend/store.sqlite`.
- Collector binary: `.runtime/collector/`. Queue, log and ingest token: `data/collector/`.

Source captures retain original synthetic content. Collector processing removes
known payload keys, not all possible personal/sensitive data. Sessions group
executions; they do not share agent memory or sandbox DB state.

## Troubleshooting

| Symptom | Check |
|---|---|
| Setup requests Python 3.14 | Install Python 3.14 with `venv`; use `python3.14`. |
| Global-package environment rejected | Install in a fresh clone; the development machine's environments are not portable. |
| Port busy | Stop the old launcher; optionally set `PIPELINE_APP_PORT=8776` for the UI. Other ports are currently fixed. |
| Bedrock access/credentials error | Check your SDK credentials, profile expiry, region and inference-profile permissions. |
| No telemetry yet | Only finished spans export; allow Collector batching. Check `data/collector/collector.log`. |
| Execution fails before C | Check the recorded error and selected scenario; failures may stop delegation. |
| Preservation does not pass | Expand processing evidence and inspect missing identities / differences in Pipeline. |

## Tests without Bedrock inference

After setup:

```sh
.runtime/checkpoint1/.venv/bin/python -m unittest discover -s app/agent1
.runtime/agent2/.venv/bin/python -m unittest discover -s app/agent2
.runtime/checkpoint1/.venv/bin/python -m unittest discover -s app/pipeline
.runtime/checkpoint1/.venv/bin/python -m unittest discover -s app/agent3
.runtime/agent3-langchain/.venv/bin/python -m unittest discover -s app/agent3-langchain
```

## Publishing the repository

Commit source code, UI, Collector configuration, requirements, `scripts/` and this
README. `.gitignore` excludes environments, runtime credentials, captures, SQLite,
logs and local proof artifacts. It cannot remove files already tracked by Git;
review staged files before pushing. Historical drafts and superseded launchers were moved to a separate sibling
archive. Current technical specifications are in `docs/`. Saved runtime telemetry
is retained locally and excluded from Git.

There is no license declaration yet; choose one before public distribution.
This workspace is not being pushed or published by these scripts.

## Validation scope

The three-agent execution is demonstrated on macOS ARM64 / Python 3.14.
The setup's planning and checksum/extraction logic were checked locally.
**A fresh isolated installation and Linux/Intel execution have not yet been
validated.** Do that before calling this a verified one-command installer.
This is a local prototype, not a production service with HA or end-user auth.

## Project layout

Everything needed for the local application is inside this one project folder:

```text
observability/
├── app/         Application code: agents, UI and consolidation backend
├── data/        Backend information: captures, trace database and Collector state
├── scripts/     Install and start commands
├── docs/        Solution and technical contracts
├── .runtime/    Installed libraries and Collector (hidden, not committed)
└── README.md
```

`data/captures/` holds native and source OTel evidence for each agent and workflow.
`data/backend/store.sqlite` holds consolidated telemetry and retained OTLP batches.
`data/collector/` holds the export queue, logs and local ingest token.
`data/proofs/` holds historical onboarding evidence; those historical hashes and
paths describe the earlier checkpoint, not the reorganized source tree.
All generated backend information is together under `data/`, outside application
code and excluded from Git. Existing captures were preserved during reorganization.

`app/agent3/` contains shared HTTP/policy helpers and adapter regression tests.
The active third agent is in `app/agent3-langchain/`.

On this same Mac the installed runtimes were preserved, so start with:

```sh
python3.14 scripts/run.py
```

For a fresh clone on another machine, run setup first as described above.
