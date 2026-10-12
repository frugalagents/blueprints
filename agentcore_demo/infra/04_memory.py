"""Idempotent provisioner for the Advisor Copilot AgentCore Memory (FEAT-003).

This script owns the final slice of Phase 4:

  1. Create (or reuse) ONE short-term AgentCore Memory named
     'advisor-copilot-memory' (eventExpiryDuration=30 days, NO memoryStrategies,
     AWS-managed encryption). Poll get_memory to status==ACTIVE, inspecting
     failureReason on FAILED. Capture memory.id + memory.arn.
  2. Merge-write memory_id + memory_arn into the gitignored infra/outputs.json
     (never clobbering guardrail/role/gateway keys) and write MEMORY_ID into the
     gitignored .env.
  3. READ-ONLY guardrail pin (preflight): read guardrail_id + guardrail_version
     from infra/outputs.json and call bedrock.get_guardrail to confirm the
     existing guardrail is READY with all four policy types. This NEVER calls
     create_guardrail / create_guardrail_version and NEVER re-runs
     infra/01_guardrail.py (which would publish a new, climbing version). It only
     re-asserts and pins the version already recorded.

Idempotency: create-if-absent-by-name (list_memories -> key 'memories', matched
by name). Re-running creates no duplicate Memory and publishes no guardrail
version.

------------------------------------------------------------------------------
MEMORY SESSION CONTRACT (for the Phase 5 swap)
------------------------------------------------------------------------------
The CONTROL client ('bedrock-agentcore-control', used here) only provisions the
Memory resource. Turns are persisted/read by the DATA-PLANE client
('bedrock-agentcore'), NOT the control client. Phase 5 will replace the
in-process SESSIONS singleton in agent/memory.py with a Memory-backed
implementation exposing the SAME three methods, so agent/agent.py call sites are
untouched:

    agent/memory.py method            AgentCore Memory event model
    --------------------------------  -------------------------------------------
    append_turn(session_id, role,     create_event / put turn with
                content)                memoryId = this resource's memory_id,
                                        sessionId = session_id (the shim's key),
                                        actorId  = the advisor id (e.g. "FA-77")
                                        so turns are scoped per advisor,
                                        payload carries {role, content}.
    get_history(session_id)           list_events(memoryId, sessionId, actorId)
                                        -> [{"role", "content"}, ...] oldest first.
    list_sessions()                   list distinct sessionIds for the actor
                                        (list_sessions / list_events paging).

memoryId is this resource; sessionId is the shim's session_id; actorId is the
advisor id. Short-term only (30-day event expiry, no long-term extraction
strategy) because the Phase 2 shim is pure conversation history.

Hardening note: a customer-managed KMS key (encryptionKeyArn) is the production
option; this POC uses the AWS-managed key (encryptionKeyArn omitted).

Usage:
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/04_memory.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import boto3

# --- constants ---------------------------------------------------------------

DEFAULT_REGION = "us-east-1"

# AgentCore Memory names must match [a-zA-Z][a-zA-Z0-9_]{0,47} (no hyphens),
# unlike the gateway/guardrail resources, so use underscores here.
MEMORY_NAME = "advisor_copilot_memory"
MEMORY_DESCRIPTION = "Advisor Copilot short-term session memory"
EVENT_EXPIRY_DAYS = 30

# Expected guardrail policy keys in a get_guardrail response (all four).
GUARDRAIL_POLICY_KEYS = (
    "topicPolicy",
    "sensitiveInformationPolicy",
    "wordPolicy",
    "contextualGroundingPolicy",
)

# Terminal / in-progress memory states (introspected shapes, boto3 1.43.111).
MEMORY_ACTIVE = "ACTIVE"
MEMORY_FAILED = "FAILED"

POLL_INTERVAL_SECONDS = 5
POLL_MAX_ATTEMPTS = 60  # ~5 minutes

OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"


def _region() -> str:
    return os.environ.get("AWS_REGION") or DEFAULT_REGION


# --- outputs.json / .env merge helpers (mirror infra/01_guardrail.py) --------


def _read_outputs() -> dict:
    if OUTPUTS_PATH.exists():
        try:
            return json.loads(OUTPUTS_PATH.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def write_outputs(updates: dict) -> None:
    """Merge `updates` into infra/outputs.json without clobbering other keys."""
    data = _read_outputs()
    data.update(updates)
    OUTPUTS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def write_env_value(key: str, value: str) -> None:
    """Set key=value in the gitignored .env, replacing an existing line."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"{key}={value}\n")
        return
    lines = ENV_PATH.read_text().splitlines()
    prefix = f"{key}="
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


# --- read-only guardrail pin -------------------------------------------------


def confirm_guardrail(region: str) -> None:
    """READ-ONLY: confirm the already-recorded guardrail is READY + 4 policies.

    Reads guardrail_id/guardrail_version from infra/outputs.json and calls
    get_guardrail. Never creates or publishes a guardrail version.
    """
    outputs = _read_outputs()
    guardrail_id = outputs.get("guardrail_id")
    guardrail_version = outputs.get("guardrail_version")
    if not guardrail_id or not guardrail_version:
        raise SystemExit(
            "guardrail_id/guardrail_version missing from infra/outputs.json; "
            "run infra/01_guardrail.py once first (FEAT not in this phase)."
        )

    bedrock = boto3.client("bedrock", region_name=region)
    resp = bedrock.get_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=str(guardrail_version),
    )

    status = resp.get("status")
    if status != "READY":
        raise SystemExit(
            f"Guardrail {guardrail_id} v{guardrail_version} status is {status!r}, "
            "expected READY."
        )

    missing = [k for k in GUARDRAIL_POLICY_KEYS if k not in resp]
    if missing:
        raise SystemExit(
            f"Guardrail {guardrail_id} v{guardrail_version} missing policy "
            f"types: {missing}"
        )

    print(
        f"Guardrail pin confirmed: status=READY, all four policy types present "
        f"(pinned version {guardrail_version}). No new version published."
    )


# --- memory provisioning -----------------------------------------------------


def _matches_name(mem: dict) -> bool:
    """True if a list_memories summary belongs to MEMORY_NAME.

    list_memories summaries carry no 'name' field; the memory id is
    '{name}-{suffix}', so match on the id prefix (fall back to an explicit
    'name' key if a future API revision adds one).
    """
    if mem.get("name") == MEMORY_NAME:
        return True
    mem_id = mem.get("id", "")
    return mem_id == MEMORY_NAME or mem_id.startswith(MEMORY_NAME + "-")


def find_existing_memory(client) -> dict | None:
    """Return the Memory summary matching MEMORY_NAME, else None.

    Idempotency key: name (encoded as the memory id prefix). list_memories
    returns the 'memories' list.
    """
    try:
        paginator = client.get_paginator("list_memories")
    except Exception:  # paginator may be unavailable; fall back to manual loop
        paginator = None

    if paginator is not None:
        for page in paginator.paginate():
            for mem in page.get("memories", []):
                if _matches_name(mem):
                    return mem
        return None

    next_token = None
    while True:
        kwargs = {"nextToken": next_token} if next_token else {}
        page = client.list_memories(**kwargs)
        for mem in page.get("memories", []):
            if _matches_name(mem):
                return mem
        next_token = page.get("nextToken")
        if not next_token:
            return None


def create_memory(client) -> str:
    """Create the short-term Memory and return its id.

    Short-term only: NO memoryStrategies. AWS-managed encryption: encryptionKeyArn
    is omitted (a customer-managed KMS CMK is the hardening option).
    """
    resp = client.create_memory(
        name=MEMORY_NAME,
        description=MEMORY_DESCRIPTION,
        eventExpiryDuration=EVENT_EXPIRY_DAYS,
    )
    memory = resp.get("memory", resp)
    return memory["id"]


def poll_to_active(client, memory_id: str) -> dict:
    """Poll get_memory until ACTIVE; raise on FAILED or timeout."""
    for _ in range(POLL_MAX_ATTEMPTS):
        resp = client.get_memory(memoryId=memory_id)
        memory = resp.get("memory", resp)
        status = memory.get("status")
        if status == MEMORY_ACTIVE:
            return memory
        if status == MEMORY_FAILED:
            reason = memory.get("failureReason", "(no failureReason)")
            raise SystemExit(f"Memory {memory_id} FAILED: {reason}")
        print(f"  memory {memory_id} status={status}; waiting...")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise SystemExit(
        f"Memory {memory_id} did not reach ACTIVE within "
        f"{POLL_MAX_ATTEMPTS * POLL_INTERVAL_SECONDS}s."
    )


def main() -> None:
    region = _region()

    # Preflight: read-only guardrail confirm/pin (no mutation).
    confirm_guardrail(region)

    client = boto3.client("bedrock-agentcore-control", region_name=region)

    existing = find_existing_memory(client)
    if existing:
        memory_id = existing["id"]
        print(f"Reusing existing memory '{MEMORY_NAME}': {memory_id}")
    else:
        memory_id = create_memory(client)
        print(f"Created memory '{MEMORY_NAME}': {memory_id}")

    memory = poll_to_active(client, memory_id)
    memory_arn = memory.get("arn", "")
    print(f"Memory ACTIVE: id={memory_id} arn={memory_arn}")

    write_outputs({"memory_id": memory_id, "memory_arn": memory_arn})
    write_env_value("MEMORY_ID", memory_id)
    print(f"Wrote memory_id + memory_arn to {OUTPUTS_PATH} (gitignored)")
    print(f"Wrote MEMORY_ID to {ENV_PATH} (gitignored)")


if __name__ == "__main__":
    main()
