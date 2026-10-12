# Advisor Copilot — AgentCore Runtime container (CAP-D, task 5.3).
#
# ARM64 (Graviton) per the AgentCore container contract — x86 images will not
# start. Protocol: HTTP (GET /ping + POST /invocations SSE) served by
# agent/app.py (FastAPI + uvicorn) on port 8080. Built in the cloud via
# CodeBuild by the AgentCore starter toolkit (no local Docker in this env).
#
# Python is PINNED to 3.12 (the project's .python-version and an
# AgentCore-supported runtime) rather than inheriting the host interpreter.
# This repo-root Dockerfile is picked up verbatim by the starter toolkit.
#
# CMD matches the toolkit contract: run the entrypoint module under
# `opentelemetry-instrument` so ADOT emits model/tool spans (input, output,
# tool calls, latency, tokens) to CloudWatch GenAI Observability (task 5.2).
FROM --platform=linux/arm64 ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1

# Install the agent's runtime dependencies first for better layer caching.
COPY agent/requirements.txt agent/requirements.txt
RUN uv pip install -r agent/requirements.txt

# ADOT distro (observability). Also pinned in agent/requirements.txt; installed
# here explicitly so the `opentelemetry-instrument` entrypoint is always present.
RUN uv pip install "aws-opentelemetry-distro>=0.18.0,<1.0.0"

# Copy the whole project (respects .dockerignore). The agent imports both the
# `agent` package and the sibling `mocks` package (local-tools fallback), so the
# build context must be the repo root.
COPY . .

# Non-root user per the container-security contract.
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080

# agent/app.py starts uvicorn on 0.0.0.0:8080 under its __main__ block, serving
# /ping and /invocations. AgentCore routes traffic internally (TLS terminated at
# the platform); do NOT expose 8080 to the internet directly.
CMD ["opentelemetry-instrument", "python", "-m", "agent.app"]
