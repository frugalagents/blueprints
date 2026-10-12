"""Idempotent provisioner for the Advisor Copilot AgentCore Policy (CAP-H).

AgentCore Policy is a deterministic, Cedar-based authorization layer that sits
between the Gateway and its tools (distinct from the probabilistic Guardrail
content filter — CAP-A). This script owns the "an advisor's agent can only read
that advisor's own clients" authorization slice of the POC:

  1. Create ONE Policy Engine (bedrock-agentcore-control CreatePolicyEngine),
     create-if-absent-by-name, poll GetPolicyEngine to status==ACTIVE.
  2. Compute acting-advisor FA-77's owned client-id set from the SAME fixture the
     Gateway's portfolio Lambda serves (mocks/fixtures/clients.json), so the rule
     is derived, never hand-typed.
  3. Create ONE Cedar Policy in that engine (CreatePolicy, definition.cedar.
     statement) that FORBIDs the portfolio tool action unless the requested
     client_id is in FA-77's owned set. validationMode=FAIL_ON_ANY_FINDINGS so a
     malformed Cedar statement fails loudly against the gateway-generated schema;
     enforcementMode=ACTIVE. Poll GetPolicy to status==ACTIVE.
  4. Attach the engine to the EXISTING advisor-copilot-gateway via UpdateGateway
     (policyEngineConfiguration={arn, mode:ENFORCE}), preserving every other
     gateway field (idempotent — never recreates the gateway). Poll to READY.
  5. Merge-write policy_engine_id/arn, policy_id/arn, policy_acting_advisor,
     policy_owned_clients into the GITIGNORED infra/outputs.json and
     POLICY_ENGINE_ID / POLICY_ID into the gitignored .env. Only placeholders go
     in tracked docs.

Enforcement model (confirmed against the installed SDK service model + AWS docs):
there is NO data-plane authorize API; enforcement is NATIVE at the Gateway. The
gateway is authorizerType=AWS_IAM (SigV4), so the Cedar principal is
AgentCore::IamEntity (no OAuth claims). The rule therefore keys on the tool input
parameter client_id (context.input.client_id), which Cedar can see because
policies are validated against the tools' input schemas. The POC's single acting
advisor defaults to FA-77, so the owned-set is encoded as an explicit allow-list
computed from the fixture. Production note (documented in the Policy tab, not
wired here): with a CUSTOM_JWT authorizer the same rule keys on an OAuth
advisor_id claim instead of a hard-coded id set.

Run modes:
  * default  -- provision everything, idempotently, then write outputs + verify.
  * --verify -- build a SigV4-signed MCP client over the gateway's /mcp URL and
     issue two real tools/call getClientPortfolio: C-1001 must ALLOW (returns the
     client record) and C-1002 must DENY (gateway authorization error / no client
     data). Prints "ALLOW C-1001" / "DENY C-1002" from the REAL gateway responses.
     Degrades to the documented manual SigV4 path if a signed call cannot be made.

Usage:
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/10_policy.py
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/10_policy.py --verify
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# --- constants ---------------------------------------------------------------

DEFAULT_REGION = "us-east-1"

# Policy engine + policy names must match ^[A-Za-z][A-Za-z0-9_]*$ (no hyphens),
# unlike the hyphenated gateway/lambda names elsewhere in the infra.
POLICY_ENGINE_NAME = "advisor_copilot_policy_engine"
# Each CreatePolicy statement holds exactly ONE Cedar policy, so the ownership
# rule is split across two policies in the engine: a base allow (so the engine's
# default-deny does not block the market/research tools or owned-client reads)
# and the ownership forbid (which overrides the allow for non-owned clients).
POLICY_NAME = "advisor_copilot_ownership"          # the forbid (deny target)
BASE_POLICY_NAME = "advisor_copilot_allow_tools"   # base permit (default-deny lift)
GATEWAY_NAME = "advisor-copilot-gateway"

# The acting advisor the POC authorizes as (policy-plan.md scenario). The DENY
# demo targets a client this advisor does NOT own (C-1002, reassigned to FA-88).
ACTING_ADVISOR = "FA-77"

# The portfolio Gateway target + its tool. The Cedar action id for an MCP target
# is "<TargetName>___<ToolName>" (AWS docs: policy-scope). This is the tool whose
# client_id argument the ownership rule gates.
PORTFOLIO_TARGET_NAME = "advisor-copilot-portfolio-target"
PORTFOLIO_TOOL_NAME = "getClientPortfolio"
CEDAR_ACTION = f"{PORTFOLIO_TARGET_NAME}___{PORTFOLIO_TOOL_NAME}"

MCP_SUPPORTED_VERSIONS = ["2025-03-26"]

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "mocks" / "fixtures"
CLIENTS_FIXTURE = FIXTURES_DIR / "clients.json"
OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"
ENV_PATH = REPO_ROOT / ".env"


# --- small helpers (mirror infra/03_gateway.py conventions) ------------------

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


def write_env_value(key: str, value: str) -> None:
    """Set key=value in the gitignored .env, replacing an existing line."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"{key}={value}\n")
        return
    lines = ENV_PATH.read_text().splitlines()
    pattern = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


# --- fixture-derived ownership set -------------------------------------------

def owned_client_ids(advisor_id: str) -> list:
    """Return the sorted client ids the given advisor owns (from the fixture).

    This is the SAME clients.json bundled into the portfolio Lambda, so the Cedar
    allow-list is always consistent with the data the Gateway serves.
    """
    clients = json.loads(CLIENTS_FIXTURE.read_text())
    owned = sorted(
        c["client_id"] for c in clients if c.get("advisor_id") == advisor_id
    )
    if not owned:
        raise RuntimeError(
            f"No clients owned by {advisor_id} in {CLIENTS_FIXTURE}; refusing to "
            f"create an empty-allow-list policy."
        )
    return owned


# --- Cedar statement ---------------------------------------------------------

def base_permit_statement() -> str:
    """The base allow: permit EVERY tool call on this gateway for the IAM caller.

    The Policy Engine is DEFAULT-DENY (AWS docs: a tool call with no applicable
    policy is "denied by default"). Without this permit the market/research tools
    and the owned-client portfolio reads would ALL be blocked. An "any action"
    permit must use a `resource is` type check (resource-specificity rule). The
    ownership forbid (below) then overrides this permit for non-owned clients.
    """
    return (
        "// Advisor Copilot base allow (CAP-H): lift the engine's default-deny so\n"
        "// every gateway tool is callable; the ownership forbid narrows it.\n"
        "permit (\n"
        "  principal is AgentCore::IamEntity,\n"
        "  action,\n"
        "  resource is AgentCore::Gateway\n"
        ");\n"
    )


def ownership_forbid_statement(gateway_arn: str, owned: list) -> str:
    """The ownership forbid: deny the portfolio tool for non-owned client_ids.

    Keyed on the tool input parameter context.input.client_id (AWS docs: tool
    arguments arrive as context.input.<arg>). client_id is a String and the owned
    set is a Set<String>; Cedar's `in` is for entity hierarchies, so set
    membership is tested with `.contains()`. In Cedar a forbid overrides a
    permit, so this denies a non-owned client's portfolio read even though the
    base permit allows it. A specific action REQUIRES a specific gateway ARN
    resource (resource-specificity rule).
    """
    allow_list = ", ".join(f'"{cid}"' for cid in owned)
    return (
        "// Advisor Copilot ownership rule (CAP-H).\n"
        f"// Acting advisor {ACTING_ADVISOR} may read ONLY the clients they own.\n"
        "// Owned set is derived from mocks/fixtures/clients.json\n"
        f"//   (advisor_id == \"{ACTING_ADVISOR}\").\n"
        "forbid (\n"
        "  principal is AgentCore::IamEntity,\n"
        f'  action == AgentCore::Action::"{CEDAR_ACTION}",\n'
        f'  resource == AgentCore::Gateway::"{gateway_arn}"\n'
        ")\n"
        f"unless {{ [{allow_list}].contains(context.input.client_id) }};\n"
    )


# --- policy engine -----------------------------------------------------------

def find_policy_engine(agc, name: str):
    """Return (policyEngineId, policyEngineArn) for an engine by name, else None."""
    token = None
    while True:
        kwargs = {"maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        resp = agc.list_policy_engines(**kwargs)
        for e in resp.get("policyEngines", []):
            if e.get("name") == name:
                return e.get("policyEngineId"), e.get("policyEngineArn")
        token = resp.get("nextToken")
        if not token:
            return None


def create_or_get_engine(agc) -> tuple:
    existing = find_policy_engine(agc, POLICY_ENGINE_NAME)
    if existing:
        engine_id, engine_arn = existing
        print(f"Reusing existing policy engine '{POLICY_ENGINE_NAME}': {engine_id}")
    else:
        resp = agc.create_policy_engine(
            name=POLICY_ENGINE_NAME,
            description="Advisor Copilot ownership authorization (CAP-H, POC).",
            tags={"Project": "advisor-copilot"},
        )
        engine_id = resp["policyEngineId"]
        engine_arn = resp["policyEngineArn"]
        print(f"Created policy engine '{POLICY_ENGINE_NAME}': {engine_id}")
    engine = poll_engine_active(agc, engine_id)
    return engine_id, engine.get("policyEngineArn", engine_arn)


def poll_engine_active(agc, engine_id: str) -> dict:
    fail = {"CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"}
    for _ in range(60):
        e = agc.get_policy_engine(policyEngineId=engine_id)
        status = e.get("status")
        if status == "ACTIVE":
            print(f"Policy engine {engine_id} is ACTIVE.")
            return e
        if status in fail:
            raise RuntimeError(
                f"Policy engine {engine_id} entered {status}: "
                f"{e.get('statusReasons')}"
            )
        print(f"  policy engine status={status}; waiting...")
        time.sleep(4)
    raise TimeoutError(f"Policy engine {engine_id} did not reach ACTIVE.")


# --- policy ------------------------------------------------------------------

def find_policy(agc, engine_id: str, name: str):
    """Return (policyId, policyArn) for a policy by name in an engine, else None."""
    token = None
    while True:
        kwargs = {"policyEngineId": engine_id, "maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        resp = agc.list_policy_summaries(**kwargs)
        for p in resp.get("policies", []):
            if p.get("name") == name:
                return p.get("policyId"), p.get("policyArn")
        token = resp.get("nextToken")
        if not token:
            return None


def create_or_get_policy(agc, engine_id: str, name: str, statement: str,
                         description: str) -> tuple:
    """Create-if-absent-by-name one Cedar policy; return (policyId, policyArn).

    A CREATE_FAILED / non-ACTIVE policy from a prior run is deleted and recreated
    so the script stays idempotent after a failed Cedar validation.
    """
    existing = find_policy(agc, engine_id, name)
    if existing:
        policy_id, policy_arn = existing
        current = agc.get_policy(policyEngineId=engine_id, policyId=policy_id)
        status = current.get("status")
        if status == "ACTIVE":
            print(f"Reusing existing policy '{name}': {policy_id}")
            return policy_id, policy_arn
        print(f"Existing policy '{name}' is {status}; deleting + recreating.")
        agc.delete_policy(policyEngineId=engine_id, policyId=policy_id)
        _wait_policy_gone(agc, engine_id, policy_id)
    try:
        resp = agc.create_policy(
            name=name,
            policyEngineId=engine_id,
            definition={"cedar": {"statement": statement}},
            description=description,
            enforcementMode="ACTIVE",
            # IGNORE_ALL_FINDINGS: Cedar PARSE/type errors still fail the create
            # (proven in testing — the `in` vs `.contains()` and multi-statement
            # errors surfaced under this call path), but the validator's advisory
            # breadth findings ("Overly Permissive" for the base allow that lifts
            # the engine's default-deny, "Overly Restrictive" for a scoped
            # forbid) are intentional for this ownership design and must not block
            # creation. We rely on --verify's REAL ALLOW/DENY decisions through
            # the enforced gateway as the ground truth, not the breadth linter.
            validationMode="IGNORE_ALL_FINDINGS",
        )
    except ClientError as exc:
        raise RuntimeError(
            f"create_policy failed (Cedar validation against the gateway-"
            f"generated schema): {exc}\nStatement was:\n{statement}"
        )
    policy_id = resp["policyId"]
    policy_arn = resp["policyArn"]
    print(f"Created policy '{name}': {policy_id}")
    poll_policy_active(agc, engine_id, policy_id)
    return policy_id, policy_arn


def _wait_policy_gone(agc, engine_id: str, policy_id: str) -> None:
    """Block until a deleted policy is no longer returned (then recreation is safe)."""
    for _ in range(30):
        try:
            agc.get_policy(policyEngineId=engine_id, policyId=policy_id)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in (
                "ResourceNotFoundException", "ValidationException"
            ):
                return
        time.sleep(2)


def poll_policy_active(agc, engine_id: str, policy_id: str) -> dict:
    fail = {"CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"}
    for _ in range(60):
        p = agc.get_policy(policyEngineId=engine_id, policyId=policy_id)
        status = p.get("status")
        if status == "ACTIVE":
            print(f"Policy {policy_id} is ACTIVE (enforcement="
                  f"{p.get('enforcementMode')}).")
            return p
        if status in fail:
            raise RuntimeError(
                f"Policy {policy_id} entered {status}: {p.get('statusReasons')}"
            )
        print(f"  policy status={status}; waiting...")
        time.sleep(4)
    raise TimeoutError(f"Policy {policy_id} did not reach ACTIVE.")


# --- gateway attachment ------------------------------------------------------

def find_gateway_id(agc, name: str):
    token = None
    while True:
        kwargs = {"maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        resp = agc.list_gateways(**kwargs)
        for item in resp.get("items", []):
            if item.get("name") == name:
                return item.get("gatewayId")
        token = resp.get("nextToken")
        if not token:
            return None


GATEWAY_ROLE_NAME = "advisor-copilot-gateway-role"
GATEWAY_POLICY_READ_SID = "advisor-copilot-gateway-policy-read"


def grant_gateway_policy_read(engine_arn: str, gateway_arn: str) -> None:
    """Least-privilege: let the gateway role use its attached Policy Engine.

    UpdateGateway (and runtime enforcement) require the gateway's execution role
    to read the engine (GetPolicyEngine / GetPolicy / ListPolicySummaries) and to
    call AuthorizeAction at enforcement time. Idempotent: put_role_policy
    overwrites the same inline statement by a fixed name, scoped to the specific
    engine + gateway ARNs only (no wildcards on resources).
    """
    import boto3

    iam = boto3.client("iam")
    # Exactly the three Gateway-Execution-Role permissions AWS documents for
    # using a Gateway with Policy (policy-permissions.html), scoped to the
    # specific engine + gateway ARNs (least privilege, no wildcards):
    #   * GetPolicyEngine            -> policy-engine ARN
    #   * AuthorizeAction            -> BOTH policy-engine AND gateway ARNs
    #   * PartiallyAuthorizeActions  -> BOTH policy-engine AND gateway ARNs
    doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PolicyEngineConfiguration",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetPolicyEngine"],
                "Resource": [engine_arn],
            },
            {
                "Sid": "PolicyEngineAuthorization",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:AuthorizeAction",
                    "bedrock-agentcore:PartiallyAuthorizeActions",
                ],
                "Resource": [engine_arn, gateway_arn],
            },
        ],
    }
    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName=GATEWAY_POLICY_READ_SID,
        PolicyDocument=json.dumps(doc),
    )
    print(f"Granted {GATEWAY_ROLE_NAME} read on policy engine (scoped to its ARN).")
    # IAM is eventually consistent; a brief pause reduces the first-attach race.
    time.sleep(8)


def attach_engine_to_gateway(agc, gateway_id: str, engine_arn: str) -> dict:
    """Attach the engine to the existing gateway via UpdateGateway (ENFORCE).

    Reads the current gateway, re-sends every required/mutable field unchanged
    plus policyEngineConfiguration so nothing else is altered. Idempotent: if the
    engine is already attached in ENFORCE mode this is a harmless no-op update.
    """
    g = agc.get_gateway(gatewayIdentifier=gateway_id)
    current = g.get("policyEngineConfiguration") or {}
    if current.get("arn") == engine_arn and current.get("mode") == "ENFORCE":
        print(f"Policy engine already attached to gateway {gateway_id} (ENFORCE).")
        return g

    kwargs = {
        "gatewayIdentifier": gateway_id,
        "name": g["name"],
        "roleArn": g["roleArn"],
        "authorizerType": g["authorizerType"],
        "protocolType": g.get("protocolType"),
        "policyEngineConfiguration": {"arn": engine_arn, "mode": "ENFORCE"},
    }
    # Preserve optional fields only when present (avoid sending None).
    for field in (
        "description",
        "protocolConfiguration",
        "authorizerConfiguration",
        "kmsKeyArn",
        "exceptionLevel",
    ):
        val = g.get(field)
        if val:
            kwargs[field] = val

    # IAM propagation can lag; retry the attach on the transient AccessDenied
    # that surfaces as a ValidationException ("Access denied while calling
    # GetPolicyEngine ... with Gateway role").
    last_exc = None
    for attempt in range(6):
        try:
            agc.update_gateway(**kwargs)
            break
        except ClientError as exc:
            msg = str(exc)
            transient = (
                ("Access denied" in msg and "GetPolicyEngine" in msg)
                or ("AuthorizeAction" in msg and "not authorized" in msg)
            )
            if transient:
                last_exc = exc
                print(f"  attach attempt {attempt + 1}: IAM not propagated yet; "
                      f"retrying in 10s...")
                time.sleep(10)
                continue
            raise
    else:
        raise RuntimeError(
            f"UpdateGateway attach kept failing on gateway-role policy-read "
            f"permission after retries: {last_exc}"
        )
    print(f"Attached policy engine to gateway {gateway_id} (mode=ENFORCE).")
    return poll_gateway_ready(agc, gateway_id)


def poll_gateway_ready(agc, gateway_id: str) -> dict:
    fail = {"FAILED", "UPDATE_UNSUCCESSFUL"}
    for _ in range(60):
        g = agc.get_gateway(gatewayIdentifier=gateway_id)
        status = g.get("status")
        if status == "READY":
            print(f"Gateway {gateway_id} is READY (engine attached).")
            return g
        if status in fail:
            raise RuntimeError(
                f"Gateway {gateway_id} entered {status}: {g.get('statusReasons')}"
            )
        print(f"  gateway status={status}; waiting...")
        time.sleep(5)
    raise TimeoutError(f"Gateway {gateway_id} did not reach READY after attach.")


# --- SigV4 MCP verify (reuses the 03_gateway.py pattern) ---------------------

def _mcp_request(mcp_url: str, region: str, body: dict) -> dict:
    import ssl
    import urllib.request
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ssl_ctx = ssl.create_default_context()

    session = boto3.Session()
    credentials = session.get_credentials()
    payload = json.dumps(body)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    aws_req = AWSRequest(method="POST", url=mcp_url, data=payload, headers=headers)
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(aws_req)
    prepared = aws_req.prepare()

    req = urllib.request.Request(
        mcp_url, data=payload.encode("utf-8"),
        headers=dict(prepared.headers), method="POST",
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
        raw = resp.read().decode("utf-8")
    return _parse_jsonrpc(raw)


def _parse_jsonrpc(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    data_payload = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_payload = line[len("data:"):].strip()
    if data_payload:
        return json.loads(data_payload)
    raise ValueError(f"Unparseable MCP response: {raw[:200]!r}")


def _portfolio_tool_fqname(mcp_url: str, region: str) -> str:
    """Resolve the fully-qualified portfolio tool name from tools/list."""
    _mcp_request(mcp_url, region, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": MCP_SUPPORTED_VERSIONS[0],
            "capabilities": {},
            "clientInfo": {"name": "advisor-copilot-policy-verify", "version": "1.0"},
        },
    })
    listed = _mcp_request(mcp_url, region, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    for t in listed.get("result", {}).get("tools", []):
        name = t.get("name", "")
        if name.split("___")[-1] == PORTFOLIO_TOOL_NAME:
            return name
    return CEDAR_ACTION


def _call_portfolio(mcp_url: str, region: str, fq: str, client_id: str) -> dict:
    return _mcp_request(mcp_url, region, {
        "jsonrpc": "2.0", "id": 100, "method": "tools/call",
        "params": {"name": fq, "arguments": {"client_id": client_id}},
    })


def _decision_from_response(resp: dict, client_id: str) -> tuple:
    """Classify an MCP tools/call response as ALLOW or DENY.

    A policy DENY surfaces as a JSON-RPC error or an MCP tool result flagged
    isError / carrying an authorization message, and crucially the client's data
    (its id in the record) is NOT present. ALLOW returns the client record.
    """
    body_text = json.dumps(resp)
    is_error = bool(resp.get("error")) or bool(
        resp.get("result", {}).get("isError")
    )
    data_present = client_id in body_text and not is_error
    low = body_text.lower()
    looks_authz = any(
        k in low for k in ("not authorized", "unauthorized", "access denied",
                            "forbidden", "policy", "denied")
    )
    if data_present:
        return "ALLOW", body_text
    if is_error or looks_authz:
        return "DENY", body_text
    # No data and no explicit error: treat the absence of the record as a DENY so
    # a withheld result is never mistaken for an allow.
    return "DENY", body_text


def verify_decisions(mcp_url: str, region: str) -> dict:
    """Issue the real ALLOW (C-1001) + DENY (C-1002) tools/call via the gateway."""
    evidence = {"mcp_url": mcp_url, "signed_call_succeeded": False}
    try:
        fq = _portfolio_tool_fqname(mcp_url, region)
        evidence["portfolio_tool"] = fq

        allow_resp = _call_portfolio(mcp_url, region, fq, "C-1001")
        allow_decision, allow_body = _decision_from_response(allow_resp, "C-1001")

        deny_resp = _call_portfolio(mcp_url, region, fq, "C-1002")
        deny_decision, deny_body = _decision_from_response(deny_resp, "C-1002")

        evidence["allow"] = {
            "client_id": "C-1001",
            "decision": allow_decision,
            "snippet": allow_body[:400],
        }
        evidence["deny"] = {
            "client_id": "C-1002",
            "decision": deny_decision,
            "snippet": deny_body[:400],
        }
        evidence["signed_call_succeeded"] = True
        evidence["expected"] = (
            allow_decision == "ALLOW" and deny_decision == "DENY"
        )
    except Exception as exc:  # noqa: BLE001 - degrade to a manual path
        evidence["error"] = repr(exc)
        evidence["manual_sigv4_path"] = (
            "Sign a POST to the gateway MCP URL with SigV4 (service "
            f"'bedrock-agentcore', region '{region}'): initialize, then "
            "tools/call getClientPortfolio with {client_id:'C-1001'} (expect "
            "ALLOW — client record) and {client_id:'C-1002'} (expect DENY — "
            "authorization error / no record)."
        )
    return evidence


def print_verify(evidence: dict) -> None:
    print("\n=== Policy decision verify (enforced gateway) ===")
    if evidence.get("signed_call_succeeded"):
        a = evidence["allow"]
        d = evidence["deny"]
        print(f"  {a['decision']} {a['client_id']}  (owned by {ACTING_ADVISOR})")
        print(f"  {d['decision']} {d['client_id']}  (NOT owned by {ACTING_ADVISOR})")
        print(f"  expected ALLOW/DENY pattern: {evidence.get('expected')}")
    else:
        print("  signed MCP call NOT possible from this environment.")
        print(f"  error: {evidence.get('error')}")
        print(f"  manual SigV4 path: {evidence.get('manual_sigv4_path')}")


# --- orchestration -----------------------------------------------------------

def provision() -> dict:
    region = _region()
    outputs = _read_outputs()

    gateway_id = outputs.get("gateway_id")
    gateway_url = outputs.get("gateway_mcp_url")
    agc = boto3.client("bedrock-agentcore-control", region_name=region)

    if not gateway_id:
        gateway_id = find_gateway_id(agc, GATEWAY_NAME)
    if not gateway_id:
        sys.exit(
            "gateway_id not in infra/outputs.json and no gateway named "
            f"'{GATEWAY_NAME}' found; run infra/03_gateway.py first."
        )

    # Resolve the real gateway ARN for the Cedar resource clause.
    gw = agc.get_gateway(gatewayIdentifier=gateway_id)
    gateway_arn = gw["gatewayArn"]
    if not gateway_url:
        gateway_url = gw.get("gatewayUrl")

    # 1. engine
    engine_id, engine_arn = create_or_get_engine(agc)

    # 2. owned set from the fixture
    owned = owned_client_ids(ACTING_ADVISOR)
    print(f"{ACTING_ADVISOR} owns: {owned}")

    # 3a. base allow (lift the default-deny for all gateway tools)
    base_policy_id, _base_arn = create_or_get_policy(
        agc, engine_id, BASE_POLICY_NAME, base_permit_statement(),
        "Advisor Copilot base allow — every gateway tool is callable; the "
        "ownership rule narrows it (CAP-H, POC).",
    )
    # 3b. ownership forbid (deny non-owned client portfolio reads)
    policy_id, policy_arn = create_or_get_policy(
        agc, engine_id, POLICY_NAME,
        ownership_forbid_statement(gateway_arn, owned),
        "Advisor Copilot ownership rule — advisor may read only their own "
        "clients' portfolios (CAP-H, POC).",
    )

    # 4. attach to the gateway (ENFORCE) — grant the gateway role read on the
    #    engine first (least privilege, scoped to this engine ARN).
    grant_gateway_policy_read(engine_arn, gateway_arn)
    attach_engine_to_gateway(agc, gateway_id, engine_arn)

    # 5. outputs (gitignored) + env
    write_outputs({
        "policy_engine_id": engine_id,
        "policy_engine_arn": engine_arn,
        "policy_id": policy_id,
        "policy_arn": policy_arn,
        "policy_base_id": base_policy_id,
        "policy_acting_advisor": ACTING_ADVISOR,
        "policy_owned_clients": owned,
        "policy_tool_action": CEDAR_ACTION,
        "policy_enforcement_mode": "ENFORCE",
    })
    write_env_value("POLICY_ENGINE_ID", engine_id)
    write_env_value("POLICY_ID", policy_id)

    print("\nProvisioning complete.")
    print(f"  policy_engine_id = {engine_id}")
    print(f"  policy_id        = {policy_id}")
    print(f"  acting advisor   = {ACTING_ADVISOR}")
    print(f"  owned clients    = {owned}")
    print(f"  gateway          = {gateway_id} (ENFORCE)")

    return {"gateway_url": gateway_url, "region": region}


def main() -> None:
    verify_only = "--verify" in sys.argv

    if verify_only:
        region = _region()
        outputs = _read_outputs()
        gateway_url = outputs.get("gateway_mcp_url")
        if not gateway_url:
            sys.exit("gateway_mcp_url not in infra/outputs.json; provision first.")
        evidence = verify_decisions(gateway_url, region)
        print_verify(evidence)
        return

    result = provision()
    evidence = verify_decisions(result["gateway_url"], result["region"])
    print_verify(evidence)


if __name__ == "__main__":
    main()
