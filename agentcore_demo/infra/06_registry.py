"""Idempotent provisioner for the Advisor Copilot AgentCore Registry (6.REG / CAP-E).

This script owns the Registry slice of Phase 6: a governed catalog of the agent
and its MCP tools, demonstrating the DRAFT -> PENDING_APPROVAL -> APPROVED
lifecycle with MANUAL approval governance.

What it does (all idempotent, create-or-reuse by name):

  1. Create ONE registry `advisor-copilot-registry` via the agent-registry-control
     write/governance plane. Governance is MANUAL: CreateRegistry is called with
     NO approvalConfiguration.autoApprovalRules, so records require explicit
     approval. Idempotent: ListRegistries first and reuse the existing registry
     id by name if present (CreateRegistry returns only registryArn, so the
     registryId is resolved from ListRegistries / the ARN).

  2. Register ONE AGENT record `advisor-copilot-agent` (recordType=AGENT) whose
     descriptors.a2aAgentCard.data is a compact A2A agent-card JSON referencing
     the deployed Runtime (name, description, skills, and the runtime ARN read
     from the gitignored infra/outputs.json). provenance links the record to the
     Runtime (relation=DETECTED_FROM, sourceType=AWS::BedrockAgentCore::Runtime,
     sourceId=<runtime ARN>).

  3. Register THREE MCP TOOL records (portfolio / market / research), recordType
     =MCP, descriptors.mcpServer.data carrying tool metadata derived from the
     Gateway target/tool definitions (including the Gateway MCP endpoint).

  4. Demonstrate the governance lifecycle: every created record is submitted for
     approval (SubmitRegistryRecordForApproval: DRAFT -> PENDING_APPROVAL), then
     EXACTLY ONE record (the agent record) is approved
     (UpdateRegistryRecordStatus status=APPROVED, statusReason=... -- both are
     required). The three tool records are left in PENDING_APPROVAL so the UI
     "Approve" button has a live target. Transitions already applied are skipped.

  5. Write registry_id, registry_arn, and every record's id/name/status into the
     gitignored infra/outputs.json. NO real id / ARN / account id is written to
     any tracked file -- the script reads the deployed Runtime/Gateway ARNs from
     outputs.json and derives the account id from sts.get_caller_identity.

Source linkage note: the agent-registry-control CreateRegistryRecord API
rejects a caller-supplied `provenance` ("provenance cannot be set by the
caller" -- it is reserved for the service's auto-detection flow). The deployed
Runtime / Gateway references are therefore carried INSIDE the descriptor data
(the agent card's `runtimeArn`, the MCP-server metadata's Gateway `endpoint`),
which keeps the source linkage visible without using the reserved field.

Namespace note: the agent-registry-control client is the write/governance plane
(migration DONE in boto3 1.43.111; agent-registry is the read/discovery plane).
If the namespace or its operations are genuinely unavailable in the installed
SDK/region, the script exits non-zero with a clear, retryable PAUSE message
rather than fabricating anything (Registry is a risky surface).

Usage:
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/06_registry.py
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/06_registry.py --verify
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# --- constants ---------------------------------------------------------------

DEFAULT_REGION = "us-east-1"
REGISTRY_CONTROL_NAMESPACE = "agent-registry-control"

REGISTRY_NAME = "advisor-copilot-registry"
AGENT_RECORD_NAME = "advisor-copilot-agent"

# One MCP tool record per Gateway target. The tool metadata mirrors the inline
# tool schemas registered on the Gateway targets in infra/03_gateway.py.
TOOL_RECORDS = [
    {
        "key": "portfolio",
        "record_name": "advisor-copilot-tool-portfolio",
        "server_description": (
            "Client portfolio MCP tools fronted by the Advisor Copilot Gateway: "
            "holdings, risk profile, assigned advisor and concentration flags."
        ),
        "tools": [
            {
                "name": "getClientPortfolio",
                "description": (
                    "Return the full portfolio for one client by client_id, "
                    "including holdings, risk profile, assigned advisor and any "
                    "concentration flags."
                ),
            }
        ],
    },
    {
        "key": "market",
        "record_name": "advisor-copilot-tool-market",
        "server_description": (
            "Market data MCP tools fronted by the Advisor Copilot Gateway: "
            "snapshot quotes for one or many tickers."
        ),
        "tools": [
            {
                "name": "getMarketQuote",
                "description": (
                    "Return the latest snapshot quote for one ticker (last "
                    "price, intraday change, trailing P/E, as-of timestamp)."
                ),
            },
            {
                "name": "getMarketQuotes",
                "description": (
                    "Return snapshot quotes for several tickers at once from a "
                    "comma-separated ticker list."
                ),
            },
        ],
    },
    {
        "key": "research",
        "record_name": "advisor-copilot-tool-research",
        "server_description": (
            "Internal research MCP tools fronted by the Advisor Copilot Gateway: "
            "research notes by ticker or by note id, with ratings and the "
            "disclaimer-required flag."
        ),
        "tools": [
            {
                "name": "listResearchByTicker",
                "description": (
                    "Return all internal research notes for one ticker, newest "
                    "first, each with rating, price target, analyst and summary."
                ),
            },
            {
                "name": "getResearchNote",
                "description": (
                    "Return one internal research note by note_id, including "
                    "rating, price target, analyst and the disclaimer flag."
                ),
            },
        ],
    },
]

# A2A / MCP data-schema versions accepted by the registry descriptors.
# The registry validates a2aAgentCard.data against A2A spec version "0.3", so the
# card's protocolVersion and the descriptor dataSchemaVersion must both be "0.3".
A2A_SCHEMA_VERSION = "0.3"
# mcpServer.data must comply with the official MCP server.json schema. The
# registry accepts versions "2025-07-09" and "2025-09-29" (probed live); the
# server.json requires name (reverse-DNS, one '/'), description (<=100 chars)
# and version, with the remote endpoint under `remotes` (streamable-http).
MCP_SCHEMA_VERSION = "2025-09-29"
MCP_NAMESPACE = "com.advisor-copilot"

OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"

# Terminal non-ready record states we treat as failures.
RECORD_FAIL_STATES = {"CREATE_FAILED", "UPDATE_FAILED", "REJECTED"}
RECORD_TRANSIENT_STATES = {"CREATING", "UPDATING"}


# --- outputs.json helpers (mirror infra/05_runtime.py) -----------------------


def _region() -> str:
    return os.environ.get("AWS_REGION") or DEFAULT_REGION


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


def _require_outputs(outputs: dict) -> None:
    missing = [k for k in ("runtime_arn", "gateway_id", "gateway_mcp_url")
               if not outputs.get(k)]
    if missing:
        sys.exit(
            "infra/outputs.json missing required keys for 6.REG: "
            f"{missing}. Run infra/03_gateway.py and infra/05_runtime.py first."
        )


def _client(region: str):
    """Build the agent-registry-control client, PAUSING if it is unavailable."""
    try:
        return boto3.client(REGISTRY_CONTROL_NAMESPACE, region_name=region)
    except Exception as exc:  # noqa: BLE001 - unknown service in installed SDK
        sys.exit(
            "PAUSE (retryable): the AgentCore Registry write plane "
            f"'{REGISTRY_CONTROL_NAMESPACE}' is not available in the installed "
            f"boto3/region ({region}): {exc!r}. Upgrade boto3 or enable the "
            "service, then re-run infra/06_registry.py."
        )


# --- 1. registry (manual approval) -------------------------------------------


def _registry_id_from_arn(registry_arn: str) -> str:
    """registry ARN tail is the registry id (e.g. .../registry/<id>)."""
    return registry_arn.rsplit("/", 1)[-1]


def find_registry(reg) -> tuple[str, str] | None:
    """Return (registryId, registryArn) of REGISTRY_NAME if present, else None."""
    token = None
    while True:
        kwargs: dict = {"maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        resp = reg.list_registries(**kwargs)
        for item in resp.get("registries", []):
            if item.get("name") == REGISTRY_NAME:
                return item.get("registryId"), item.get("registryArn")
        token = resp.get("nextToken")
        if not token:
            return None


def ensure_registry(reg) -> tuple[str, str]:
    """Create-if-absent the registry with MANUAL approval. Returns (id, arn)."""
    found = find_registry(reg)
    if found:
        registry_id, registry_arn = found
        print(f"Reusing existing registry '{REGISTRY_NAME}': {registry_id}")
        return registry_id, registry_arn

    # MANUAL approval governance == CreateRegistry with NO approvalConfiguration
    # (no autoApprovalRules). Records then require explicit approval.
    resp = reg.create_registry(
        name=REGISTRY_NAME,
        description=(
            "Advisor Copilot governed catalog: the deployed Strands agent and "
            "its MCP tools, with manual approval governance (POC)."
        ),
        tags={"Project": "advisor-copilot"},
    )
    registry_arn = resp["registryArn"]
    registry_id = _registry_id_from_arn(registry_arn)
    print(f"Created registry '{REGISTRY_NAME}': {registry_id}")

    # CreateRegistry can return before the registry is usable; resolve the id
    # authoritatively from ListRegistries (and let it settle for record writes).
    for _ in range(30):
        found = find_registry(reg)
        if found and found[0]:
            registry_id, registry_arn = found
            status = _registry_status(reg, registry_id)
            if status in (None, "ACTIVE", "READY", "AVAILABLE"):
                break
            if status in ("CREATE_FAILED", "FAILED"):
                sys.exit(
                    f"PAUSE: registry '{REGISTRY_NAME}' entered {status}; "
                    "inspect agent-registry-control and re-run."
                )
        time.sleep(3)
    return registry_id, registry_arn


def _registry_status(reg, registry_id: str) -> str | None:
    try:
        return reg.get_registry(registryId=registry_id).get("status")
    except ClientError:
        return None


# --- 2+3. records -------------------------------------------------------------


def _agent_card(outputs: dict) -> str:
    """Compact A2A agent-card JSON string referencing the deployed Runtime."""
    runtime_arn = outputs["runtime_arn"]
    # A2A v0.3 required top-level fields: protocolVersion, name, description,
    # url, version, capabilities, defaultInputModes, defaultOutputModes, skills.
    card = {
        "protocolVersion": A2A_SCHEMA_VERSION,
        "name": "Advisor Copilot",
        "description": (
            "Financial-advisor copilot: prepares client reviews "
            "by combining portfolio data, live market quotes and internal "
            "research, with a compliance guardrail on every response."
        ),
        # The deployed Runtime this agent is served by (reference, not a secret).
        # The A2A card requires a URL; the AgentCore invoke path is the runtime
        # ARN, so the runtime ARN is advertised as the agent's endpoint URL.
        "url": runtime_arn,
        "version": "1.0.0",
        "provider": {
            "organization": "Advisor Copilot POC",
            "url": "https://example.com/advisor-copilot",
        },
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "client-review-prep",
                "name": "Client review preparation",
                "description": (
                    "Pull a client's portfolio and concentration flags, fetch "
                    "market quotes for key positions, summarize relevant "
                    "internal research, and produce compliant talking points."
                ),
                "tags": ["portfolio", "market", "research", "compliance"],
            }
        ],
    }
    return json.dumps(card, separators=(",", ":"))


def _mcp_server_data(tool_record: dict, outputs: dict) -> str:
    """MCP server.json (schema 2025-09-29) metadata for one Gateway tool group.

    Conforms to the official MCP ServerDetail schema: `name` is reverse-DNS with
    exactly one '/', `description` is <=100 chars, `version` is semantic, and the
    Gateway MCP endpoint is advertised as a streamable-http remote. The concrete
    tool list (derived from the Gateway target schemas) is carried under `_meta`
    (publisher-provided), which the schema allows as free-form vendor metadata.
    """
    # description must be <=100 chars per the server.json schema.
    description = tool_record["server_description"]
    if len(description) > 100:
        description = description[:97].rstrip() + "..."
    data = {
        "$schema": (
            "https://static.modelcontextprotocol.io/schemas/"
            f"{MCP_SCHEMA_VERSION}/server.schema.json"
        ),
        "name": f"{MCP_NAMESPACE}/{tool_record['key']}",
        "description": description,
        "version": "1.0.0",
        # The Gateway MCP endpoint these tools are served behind.
        "remotes": [
            {"type": "streamable-http", "url": outputs["gateway_mcp_url"]}
        ],
        "_meta": {
            "io.modelcontextprotocol.registry/publisher-provided": {
                "tools": tool_record["tools"],
            }
        },
    }
    return json.dumps(data, separators=(",", ":"))


def list_records(reg, registry_id: str) -> list:
    """Return all registry records for registry_id."""
    records: list = []
    token = None
    while True:
        kwargs: dict = {"registryId": registry_id, "maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        resp = reg.list_registry_records(**kwargs)
        records.extend(resp.get("registryRecords", []))
        token = resp.get("nextToken")
        if not token:
            return records


def find_record(reg, registry_id: str, name: str) -> dict | None:
    for r in list_records(reg, registry_id):
        if r.get("name") == name:
            return r
    return None


def _wait_record_out_of_creating(reg, registry_id: str, record_id: str) -> dict:
    """Poll a record until it leaves CREATING/UPDATING; return its record dict."""
    for _ in range(30):
        rec = reg.get_registry_record(registryId=registry_id, recordId=record_id)
        status = rec.get("status")
        if status in RECORD_FAIL_STATES:
            raise RuntimeError(
                f"Record {record_id} entered {status}: "
                f"{rec.get('statusReason')}"
            )
        if status not in RECORD_TRANSIENT_STATES:
            return rec
        time.sleep(3)
    # Return whatever we last read; the caller handles non-terminal gracefully.
    return reg.get_registry_record(registryId=registry_id, recordId=record_id)


def ensure_record(reg, registry_id: str, name: str, record_type: str,
                  descriptors: dict, description: str) -> dict:
    """Create-if-absent one registry record by name. Returns its record dict.

    `provenance` is intentionally NOT passed: the API reserves it for its own
    auto-detection flow and rejects a caller-supplied value. Source linkage to
    the deployed Runtime / Gateway lives inside the descriptor data instead.
    """
    existing = find_record(reg, registry_id, name)
    if existing:
        print(f"  reusing record '{name}' ({existing.get('recordId')}, "
              f"status={existing.get('status')})")
        return _wait_record_out_of_creating(
            reg, registry_id, existing["recordId"]
        )

    reg.create_registry_record(
        registryId=registry_id,
        name=name,
        recordType=record_type,
        description=description,
        descriptors=descriptors,
    )
    # CreateRegistryRecord returns recordArn + status but no recordId; resolve
    # the recordId authoritatively by matching the name in ListRegistryRecords.
    created = find_record(reg, registry_id, name)
    if not created:
        raise RuntimeError(
            f"record '{name}' not found via ListRegistryRecords after create."
        )
    print(f"  created record '{name}' ({created.get('recordId')})")
    return _wait_record_out_of_creating(reg, registry_id, created["recordId"])


# --- 4. governance lifecycle --------------------------------------------------


def submit_for_approval(reg, registry_id: str, record: dict) -> str:
    """DRAFT -> PENDING_APPROVAL. Idempotent: skip if already past DRAFT."""
    status = record.get("status")
    record_id = record["recordId"]
    if status == "DRAFT":
        resp = reg.submit_registry_record_for_approval(
            registryId=registry_id, recordId=record_id
        )
        new_status = resp.get("status", "PENDING_APPROVAL")
        print(f"  submitted '{record.get('name')}' for approval "
              f"-> {new_status}")
        return new_status
    print(f"  '{record.get('name')}' already {status}; skip submit (idempotent)")
    return status


def approve_record(reg, registry_id: str, record: dict) -> str:
    """PENDING_APPROVAL -> APPROVED. Idempotent: skip if already APPROVED."""
    record_id = record["recordId"]
    # Re-read the current status so we never double-apply.
    current = reg.get_registry_record(
        registryId=registry_id, recordId=record_id
    ).get("status")
    if current == "APPROVED":
        print(f"  '{record.get('name')}' already APPROVED; skip (idempotent)")
        return current
    resp = reg.update_registry_record_status(
        registryId=registry_id,
        recordId=record_id,
        status="APPROVED",
        statusReason="Phase 6 governance demo",
    )
    new_status = resp.get("status", "APPROVED")
    print(f"  approved '{record.get('name')}' -> {new_status}")
    return new_status


# --- orchestration -----------------------------------------------------------


def provision(region: str) -> dict:
    outputs = _read_outputs()
    _require_outputs(outputs)
    # Resolve the account id (never hardcoded); currently unused for record
    # writes but kept to validate credentials up front and for future use.
    boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    reg = _client(region)

    # 1. Registry (manual approval).
    registry_id, registry_arn = ensure_registry(reg)

    # 2. AGENT record referencing the deployed Runtime.
    print("Registering AGENT record...")
    agent_rec = ensure_record(
        reg,
        registry_id,
        AGENT_RECORD_NAME,
        "AGENT",
        {"a2aAgentCard": {
            "data": _agent_card(outputs),
            "dataSchemaVersion": A2A_SCHEMA_VERSION,
        }},
        "Advisor Copilot Strands agent (deployed on AgentCore Runtime).",
    )

    # 3. Three MCP TOOL records referencing the Gateway.
    print("Registering MCP tool records...")
    tool_recs = []
    for tr in TOOL_RECORDS:
        rec = ensure_record(
            reg,
            registry_id,
            tr["record_name"],
            "MCP",
            {"mcpServer": {
                "data": _mcp_server_data(tr, outputs),
                "dataSchemaVersion": MCP_SCHEMA_VERSION,
            }},
            f"Advisor Copilot {tr['key']} MCP tools (via the Gateway).",
        )
        tool_recs.append(rec)

    # 4. Governance lifecycle demo.
    #    Submit EVERY record for approval (DRAFT -> PENDING_APPROVAL), then
    #    APPROVE exactly ONE (the agent record). Leave the 3 tool records in
    #    PENDING_APPROVAL so the UI approve button has a live target.
    print("Driving governance lifecycle...")
    submit_for_approval(reg, registry_id, agent_rec)
    for rec in tool_recs:
        submit_for_approval(reg, registry_id, rec)
    approve_record(reg, registry_id, agent_rec)

    # 5. Persist final statuses (re-read for ground truth).
    final = {r["recordId"]: r for r in list_records(reg, registry_id)}
    record_outputs = []
    for rec in [agent_rec, *tool_recs]:
        fresh = final.get(rec["recordId"], rec)
        record_outputs.append({
            "id": fresh["recordId"],
            "name": fresh["name"],
            "recordType": fresh["recordType"],
            "status": fresh["status"],
        })

    write_outputs({
        "registry_id": registry_id,
        "registry_arn": registry_arn,
        "registry_records": record_outputs,
        "registry_agent_record_id": agent_rec["recordId"],
    })
    print(f"Wrote registry outputs to {OUTPUTS_PATH} (gitignored).")

    return {
        "registry_id": registry_id,
        "registry_arn": registry_arn,
        "records": record_outputs,
    }


def verify(region: str) -> int:
    """List records with statuses; assert exactly 1 APPROVED, rest pending/draft."""
    outputs = _read_outputs()
    registry_id = outputs.get("registry_id")
    if not registry_id:
        print("registry_id not in outputs.json; run infra/06_registry.py first.")
        return 1
    reg = _client(region)
    records = list_records(reg, registry_id)
    if not records:
        print(f"No records found in registry {registry_id}.")
        return 1

    print(f"=== Registry {registry_id} records ===")
    approved = 0
    pending_or_draft = 0
    for r in sorted(records, key=lambda x: x.get("name", "")):
        status = r.get("status")
        print(f"  {r.get('name'):32s} {r.get('recordType'):6s} {status}")
        if status == "APPROVED":
            approved += 1
        elif status in ("PENDING_APPROVAL", "DRAFT"):
            pending_or_draft += 1

    ok = approved == 1 and pending_or_draft == len(records) - 1
    print(f"\nAPPROVED={approved} PENDING_APPROVAL/DRAFT={pending_or_draft} "
          f"total={len(records)}")
    if ok:
        print("OK: lifecycle demonstrated (exactly 1 APPROVED, rest pending/draft).")
        return 0
    print("ASSERTION FAILED: expected exactly 1 APPROVED and the rest "
          "PENDING_APPROVAL/DRAFT.")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="List records + assert the lifecycle; no writes.")
    args = parser.parse_args()
    region = _region()

    if args.verify:
        sys.exit(verify(region))

    result = provision(region)
    print("\n=== Summary ===")
    print(f"registry_id={result['registry_id']}")
    for r in result["records"]:
        print(f"  {r['name']:32s} {r['recordType']:6s} {r['status']}")


if __name__ == "__main__":
    main()
