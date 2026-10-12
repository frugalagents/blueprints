"""Idempotent provisioner for the Advisor Copilot AgentCore Gateway (FEAT-002).

This script owns the whole "mock-data behind one MCP Gateway" slice of Phase 4:

  1. Package + deploy the three Python 3.12 mock Lambdas (portfolio / market /
     research). Each zip bundles the *_lambda.py module at the zip root AND the
     fixtures/ dir, because each handler resolves its fixture JSON relative to
     the module file -- the fixtures MUST live inside the deployment package.
  2. Add a resource-based policy to each Lambda allowing principal
     bedrock-agentcore.amazonaws.com to lambda:InvokeFunction, conditioned on
     aws:SourceAccount + aws:SourceArn (the gateway ARN) -- confused-deputy
     protection. Idempotent via a FIXED StatementId (ResourceConflictException
     swallowed on re-run).
  3. Create ONE AgentCore Gateway (protocolType=MCP, authorizerType=AWS_IAM,
     roleArn=advisor-copilot-gateway-role). Poll get_gateway to status==READY.
  4. Create THREE Lambda targets (targetConfiguration.mcp.lambda + an INLINE
     toolSchema derived from the existing OpenAPI operations;
     credentialProviderConfigurations=[{credentialProviderType:GATEWAY_IAM_ROLE}]).
     Poll each get_gateway_target to status==READY, inspecting statusReasons on
     the error states (FAILED / UPDATE_UNSUCCESSFUL / SYNCHRONIZE_UNSUCCESSFUL)
     -- there is NO ACTIVE state. Fallback: if an inline-schema target FAILs on
     a schema shape, retry with targetConfiguration.mcp.openApiSchema.inlinePayload
     (still no S3).

The gateway ARN is needed for the Lambda resource policy's SourceArn, so the
gateway is created BEFORE the policies are tightened to its exact ARN.

Run modes:
  * default  -- provision everything, idempotently, then write outputs.
  * --verify -- (also run at the end of a default run) build a SigV4-signed MCP
    client over the gateway's /mcp URL (botocore SigV4Auth, service
    'bedrock-agentcore', region us-east-1), send tools/list, assert the 5 tools,
    and tools/call one per target. If a signed call cannot be made from this
    environment, the exact manual SigV4 path + the tools/list output are recorded
    as evidence instead.

Idempotency: create-if-absent-by-name throughout (lambda get_function,
list_gateways->items, list_gateway_targets->items). Re-running creates no
duplicate Lambda / Gateway / target.

Outputs (gitignored): infra/outputs.json gains portfolio/market/research lambda
ARNs, gateway_id, gateway_mcp_url, and the 3 target ids (merge-written, never
overwriting guardrail_id / role ARNs). .env gains GATEWAY_MCP_URL. No real
account id / gateway id is written to any tracked file.

Usage:
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/03_gateway.py
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/03_gateway.py --verify
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# --- constants ---------------------------------------------------------------

DEFAULT_REGION = "us-east-1"

GATEWAY_NAME = "advisor-copilot-gateway"
GATEWAY_INVOKE_STATEMENT_ID = "agentcore-gateway-invoke"
MCP_SUPPORTED_VERSIONS = ["2025-03-26"]

REPO_ROOT = Path(__file__).resolve().parent.parent
MOCKS_DIR = REPO_ROOT / "mocks"
FIXTURES_DIR = MOCKS_DIR / "fixtures"
BUILD_DIR = Path(__file__).resolve().parent / "build"
OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"
ENV_PATH = REPO_ROOT / ".env"

# Per-service definition: logical key -> (lambda function name, module stem,
# fixture file bundled into the zip, list of inline tool defs for the target).
# Tool names/descriptions are copied from the OpenAPI operations.
SERVICES = {
    "portfolio": {
        "function_name": "advisor-copilot-portfolio",
        "module": "portfolio_lambda",
        "fixture": "clients.json",
        "target_name": "advisor-copilot-portfolio-target",
        "tools": [
            {
                "name": "getClientPortfolio",
                "description": (
                    "Return the full portfolio for one client by client_id, "
                    "including holdings (ticker, shares, cost basis, sector), "
                    "risk profile, assigned advisor, and any concentration flags "
                    "such as an oversized single-name equity position."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "client_id": {
                            "type": "string",
                            "description": (
                                "Synthetic client identifier, e.g. C-1001 for "
                                "Jane Doe."
                            ),
                        }
                    },
                    "required": ["client_id"],
                },
            }
        ],
    },
    "market": {
        "function_name": "advisor-copilot-market",
        "module": "market_lambda",
        "fixture": "market.json",
        "target_name": "advisor-copilot-market-target",
        "tools": [
            {
                "name": "getMarketQuote",
                "description": (
                    "Return the latest synthetic snapshot quote for one ticker, "
                    "including last price, intraday percentage change, trailing "
                    "P/E (null for instruments without one, such as bond ETFs), "
                    "and the as-of timestamp."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Ticker symbol, e.g. NVDA.",
                        }
                    },
                    "required": ["ticker"],
                },
            },
            {
                "name": "getMarketQuotes",
                "description": (
                    "Return snapshot quotes for several tickers at once. Supply "
                    "a comma-separated list of ticker symbols; unknown tickers "
                    "are omitted from the result."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tickers": {
                            "type": "string",
                            "description": (
                                "Comma-separated ticker symbols, e.g. "
                                "NVDA,AGG,MSFT."
                            ),
                        }
                    },
                    "required": ["tickers"],
                },
            },
        ],
    },
    "research": {
        "function_name": "advisor-copilot-research",
        "module": "research_lambda",
        "fixture": "research.json",
        "target_name": "advisor-copilot-research-target",
        "tools": [
            {
                "name": "listResearchByTicker",
                "description": (
                    "Return all internal research notes for one ticker, newest "
                    "first. Each note carries a rating (e.g. Overweight, Hold, "
                    "Underweight), a price target, the publishing analyst, a "
                    "summary, and whether a compliance disclaimer is required "
                    "when the note is quoted to a client."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": (
                                "Ticker symbol to list research for, e.g. NVDA."
                            ),
                        }
                    },
                    "required": ["ticker"],
                },
            },
            {
                "name": "getResearchNote",
                "description": (
                    "Return one internal research note by its note_id, including "
                    "rating, price target, analyst, summary, and the "
                    "disclaimer-required flag."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "note_id": {
                            "type": "string",
                            "description": (
                                "Research note identifier, e.g. R-5567."
                            ),
                        }
                    },
                    "required": ["note_id"],
                },
            },
        ],
    },
}

# Order services deterministically for stable output.
SERVICE_ORDER = ["portfolio", "market", "research"]

# tools/call smoke tests exercised in --verify mode (one per target).
VERIFY_CALLS = [
    ("getClientPortfolio", {"client_id": "C-1001"}, "Jane Doe"),
    ("getMarketQuote", {"ticker": "NVDA"}, "NVDA"),
    ("getResearchNote", {"note_id": "R-5567"}, "R-5567"),
]

EXPECTED_TOOL_NAMES = {
    "getClientPortfolio",
    "getMarketQuote",
    "getMarketQuotes",
    "listResearchByTicker",
    "getResearchNote",
}


# --- small helpers -----------------------------------------------------------

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


# --- step 1: packaging -------------------------------------------------------

def build_zip(svc_key: str) -> bytes:
    """Build a Lambda deployment zip for one service.

    Layout inside the zip:
        <module>.py              (at the zip root -> importable as the handler)
        fixtures/<fixture>.json  (handler reads fixtures/ relative to module dir)

    The handler's os.path.join(dirname(__file__), 'fixtures', ...) therefore
    resolves inside /var/task on the Lambda. We bundle ALL fixture JSONs under
    fixtures/ (cheap, and keeps the layout identical to the repo) so the module
    import side effects behave exactly as locally.
    """
    svc = SERVICES[svc_key]
    module_path = MOCKS_DIR / f"{svc['module']}.py"
    if not module_path.exists():
        raise FileNotFoundError(f"missing lambda module: {module_path}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(module_path, arcname=f"{svc['module']}.py")
        for fixture in sorted(FIXTURES_DIR.glob("*.json")):
            zf.write(fixture, arcname=f"fixtures/{fixture.name}")
    data = buf.getvalue()

    # Also drop a copy under the gitignored build dir for debuggability.
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    (BUILD_DIR / f"{svc['function_name']}.zip").write_bytes(data)
    return data


# --- step 1 cont.: deploy lambdas -------------------------------------------

def deploy_lambda(lambda_client, svc_key: str, zip_bytes: bytes,
                  lambda_exec_role_arn: str) -> str:
    """Create-if-absent (else update code) a Lambda; return its ARN."""
    svc = SERVICES[svc_key]
    name = svc["function_name"]
    handler = f"{svc['module']}.handler"
    try:
        resp = lambda_client.get_function(FunctionName=name)
        arn = resp["Configuration"]["FunctionArn"]
        lambda_client.update_function_code(FunctionName=name, ZipFile=zip_bytes)
        print(f"Updated Lambda code '{name}': {arn}")
    except lambda_client.exceptions.ResourceNotFoundException:
        resp = lambda_client.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Handler=handler,
            Role=lambda_exec_role_arn,
            Code={"ZipFile": zip_bytes},
            Timeout=15,
            MemorySize=256,
            Description=f"Advisor Copilot mock {svc_key} data API (POC).",
            Tags={"Project": "advisor-copilot"},
            Publish=False,
        )
        arn = resp["FunctionArn"]
        print(f"Created Lambda '{name}': {arn}")
    return arn


def wait_lambda_active(lambda_client, name: str) -> None:
    """Wait until the function's LastUpdateStatus settles (Successful)."""
    for _ in range(30):
        cfg = lambda_client.get_function(FunctionName=name)["Configuration"]
        state = cfg.get("State")
        last = cfg.get("LastUpdateStatus")
        if state == "Active" and last in (None, "Successful"):
            return
        if last == "Failed":
            raise RuntimeError(
                f"Lambda {name} update failed: {cfg.get('LastUpdateStatusReason')}"
            )
        time.sleep(2)


# --- step 2: resource-based policy ------------------------------------------

def add_invoke_permission(lambda_client, name: str, account_id: str,
                          gateway_arn: str) -> None:
    """Idempotently grant bedrock-agentcore to invoke the Lambda (source-scoped)."""
    try:
        lambda_client.add_permission(
            FunctionName=name,
            StatementId=GATEWAY_INVOKE_STATEMENT_ID,
            Action="lambda:InvokeFunction",
            Principal="bedrock-agentcore.amazonaws.com",
            SourceAccount=account_id,
            SourceArn=gateway_arn,
        )
        print(f"Added invoke permission on '{name}' for gateway {gateway_arn}")
    except lambda_client.exceptions.ResourceConflictException:
        # Statement already present from a prior run. Re-running is a no-op; we
        # intentionally do not remove/re-add so the policy stays stable.
        print(f"Invoke permission already present on '{name}' (idempotent).")


# --- step 3: gateway ---------------------------------------------------------

def find_gateway(agc, name: str) -> str | None:
    """Return the gatewayId of an existing gateway named `name`, else None."""
    paginator_token = None
    while True:
        kwargs = {"maxResults": 50}
        if paginator_token:
            kwargs["nextToken"] = paginator_token
        resp = agc.list_gateways(**kwargs)
        for item in resp.get("items", []):
            if item.get("name") == name:
                return item.get("gatewayId")
        paginator_token = resp.get("nextToken")
        if not paginator_token:
            return None


def create_or_get_gateway(agc, gateway_role_arn: str) -> dict:
    """Create-if-absent the MCP gateway; poll to READY; return get_gateway dict."""
    gateway_id = find_gateway(agc, GATEWAY_NAME)
    if gateway_id:
        print(f"Reusing existing gateway '{GATEWAY_NAME}': {gateway_id}")
    else:
        resp = agc.create_gateway(
            name=GATEWAY_NAME,
            roleArn=gateway_role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
            description="Advisor Copilot mock-data MCP gateway (POC).",
            protocolConfiguration={
                "mcp": {
                    "supportedVersions": MCP_SUPPORTED_VERSIONS,
                    "instructions": "Advisor Copilot mock-data tools",
                    "streamingConfiguration": {"enableResponseStreaming": True},
                }
            },
        )
        gateway_id = resp["gatewayId"]
        print(f"Created gateway '{GATEWAY_NAME}': {gateway_id}")

    return poll_gateway_ready(agc, gateway_id)


def poll_gateway_ready(agc, gateway_id: str) -> dict:
    terminal_fail = {"FAILED", "UPDATE_UNSUCCESSFUL"}
    for _ in range(60):
        g = agc.get_gateway(gatewayIdentifier=gateway_id)
        status = g.get("status")
        if status == "READY":
            print(f"Gateway {gateway_id} is READY.")
            return g
        if status in terminal_fail:
            reasons = g.get("statusReasons")
            raise RuntimeError(
                f"Gateway {gateway_id} entered {status}: {reasons}"
            )
        print(f"  gateway status={status}; waiting...")
        time.sleep(5)
    raise TimeoutError(f"Gateway {gateway_id} did not reach READY in time.")


# --- step 4: targets ---------------------------------------------------------

def find_target(agc, gateway_id: str, name: str) -> str | None:
    token = None
    while True:
        kwargs = {"gatewayIdentifier": gateway_id, "maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        resp = agc.list_gateway_targets(**kwargs)
        for item in resp.get("items", []):
            if item.get("name") == name:
                return item.get("targetId")
        token = resp.get("nextToken")
        if not token:
            return None


def _inline_target_config(lambda_arn: str, tools: list) -> dict:
    return {
        "mcp": {
            "lambda": {
                "lambdaArn": lambda_arn,
                "toolSchema": {"inlinePayload": tools},
            }
        }
    }


def _openapi_fallback_config(svc_key: str) -> dict:
    """Fallback transport: OpenAPI inline YAML text (still no S3)."""
    yaml_path = MOCKS_DIR / "openapi" / f"{svc_key}.yaml"
    return {"mcp": {"openApiSchema": {"inlinePayload": yaml_path.read_text()}}}


def create_or_get_target(agc, gateway_id: str, svc_key: str,
                         lambda_arn: str) -> str:
    """Create-if-absent one Lambda target (inline schema, GATEWAY_IAM_ROLE)."""
    svc = SERVICES[svc_key]
    name = svc["target_name"]
    existing = find_target(agc, gateway_id, name)
    if existing:
        print(f"Reusing existing target '{name}': {existing}")
        poll_target_ready(agc, gateway_id, existing, svc_key, lambda_arn)
        return existing

    try:
        resp = agc.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=name,
            description=f"Advisor Copilot {svc_key} Lambda MCP target.",
            targetConfiguration=_inline_target_config(lambda_arn, svc["tools"]),
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
        target_id = resp["targetId"]
        print(f"Created target '{name}': {target_id}")
    except ClientError as exc:
        raise RuntimeError(f"create_gateway_target failed for {name}: {exc}")

    poll_target_ready(agc, gateway_id, target_id, svc_key, lambda_arn)
    return target_id


def poll_target_ready(agc, gateway_id: str, target_id: str, svc_key: str,
                      lambda_arn: str) -> None:
    """Poll a target to READY; on a schema-shape FAIL, fall back to OpenAPI."""
    error_states = {"FAILED", "UPDATE_UNSUCCESSFUL", "SYNCHRONIZE_UNSUCCESSFUL"}
    for _ in range(60):
        t = agc.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = t.get("status")
        if status == "READY":
            print(f"  target {target_id} ({svc_key}) is READY.")
            return
        if status in error_states:
            reasons = t.get("statusReasons") or []
            print(f"  target {target_id} ({svc_key}) {status}: {reasons}")
            if _looks_like_schema_error(reasons):
                print(f"  retrying target {svc_key} with openApiSchema fallback.")
                agc.update_gateway_target(
                    gatewayIdentifier=gateway_id,
                    targetId=target_id,
                    name=SERVICES[svc_key]["target_name"],
                    targetConfiguration=_openapi_fallback_config(svc_key),
                    credentialProviderConfigurations=[
                        {"credentialProviderType": "GATEWAY_IAM_ROLE"}
                    ],
                )
                # Re-poll after switching transport (no further fallback).
                return _poll_target_ready_nofallback(
                    agc, gateway_id, target_id, svc_key
                )
            raise RuntimeError(
                f"Target {target_id} ({svc_key}) entered {status}: {reasons}"
            )
        print(f"  target {target_id} ({svc_key}) status={status}; waiting...")
        time.sleep(5)
    raise TimeoutError(f"Target {target_id} ({svc_key}) did not reach READY.")


def _poll_target_ready_nofallback(agc, gateway_id, target_id, svc_key) -> None:
    error_states = {"FAILED", "UPDATE_UNSUCCESSFUL", "SYNCHRONIZE_UNSUCCESSFUL"}
    for _ in range(60):
        t = agc.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = t.get("status")
        if status == "READY":
            print(f"  target {target_id} ({svc_key}) READY via openApiSchema.")
            return
        if status in error_states:
            raise RuntimeError(
                f"Target {target_id} ({svc_key}) failed after openApiSchema "
                f"fallback: {t.get('statusReasons')}"
            )
        time.sleep(5)
    raise TimeoutError(f"Target {target_id} ({svc_key}) stuck after fallback.")


def _looks_like_schema_error(reasons: list) -> bool:
    text = " ".join(str(r) for r in reasons).lower()
    return any(k in text for k in ("schema", "inputschema", "toolschema",
                                   "payload", "validation"))


# --- step 5: SigV4 MCP enumeration + exercise -------------------------------

def _mcp_request(mcp_url: str, region: str, body: dict) -> dict:
    """Send a single SigV4-signed JSON-RPC request to the MCP endpoint.

    Uses botocore SigV4Auth (service 'bedrock-agentcore') + urllib (stdlib) so
    no extra dependency (requests) is needed. Returns the parsed JSON-RPC
    response (handles both application/json and text/event-stream replies).
    """
    import ssl
    import urllib.request
    import urllib.error
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    # Use certifi's CA bundle (shipped with botocore) so TLS verification works
    # even when the OS trust store is not wired up for this interpreter.
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - fall back to system default context
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
    """Parse a JSON-RPC body that may be plain JSON or SSE (text/event-stream)."""
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    # SSE framing: lines like "data: {...}". Take the last data: payload.
    data_payload = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_payload = line[len("data:"):].strip()
    if data_payload:
        return json.loads(data_payload)
    raise ValueError(f"Unparseable MCP response: {raw[:200]!r}")


def verify_mcp(mcp_url: str, region: str) -> dict:
    """tools/list + one tools/call per target. Returns an evidence dict.

    Degrades gracefully: if the signed call cannot be made from this environment
    the exact manual SigV4 path is recorded and the error captured, rather than
    raising -- per the acceptance criteria ("documented manual path if no signed
    call is possible").
    """
    evidence: dict = {"mcp_url": mcp_url, "signed_call_succeeded": False}
    try:
        # MCP requires an initialize handshake before tools/list.
        init = _mcp_request(mcp_url, region, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": MCP_SUPPORTED_VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": "advisor-copilot-verify", "version": "1.0"},
            },
        })
        evidence["initialize"] = init.get("result", init)

        listed = _mcp_request(mcp_url, region, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        tools = listed.get("result", {}).get("tools", [])
        tool_names = sorted(t.get("name", "").split("___")[-1] for t in tools)
        evidence["tools"] = [
            {"name": t.get("name"), "description": t.get("description")}
            for t in tools
        ]
        evidence["tool_names"] = tool_names

        found = {n.split("___")[-1] for n in
                 (t.get("name", "") for t in tools)}
        missing = EXPECTED_TOOL_NAMES - found
        evidence["expected_present"] = not missing
        if missing:
            evidence["missing_tools"] = sorted(missing)

        # Map short tool name -> fully-qualified name as advertised by the gateway.
        name_map = {t.get("name", "").split("___")[-1]: t.get("name", "")
                    for t in tools}

        calls = []
        for short_name, args, needle in VERIFY_CALLS:
            fq = name_map.get(short_name, short_name)
            try:
                res = _mcp_request(mcp_url, region, {
                    "jsonrpc": "2.0", "id": 100, "method": "tools/call",
                    "params": {"name": fq, "arguments": args},
                })
                body_text = json.dumps(res)
                calls.append({
                    "tool": short_name,
                    "arguments": args,
                    "needle": needle,
                    "needle_found": needle in body_text,
                    "result": res.get("result", res),
                })
            except Exception as exc:  # noqa: BLE001 - record and continue
                calls.append({"tool": short_name, "arguments": args,
                              "error": repr(exc)})
        evidence["calls"] = calls
        evidence["signed_call_succeeded"] = True
    except Exception as exc:  # noqa: BLE001 - degrade to manual-path evidence
        evidence["error"] = repr(exc)
        evidence["manual_sigv4_path"] = _manual_sigv4_doc(mcp_url, region)
    return evidence


def _manual_sigv4_doc(mcp_url: str, region: str) -> str:
    return (
        "Sign a POST to the gateway MCP URL with AWS SigV4, service name "
        f"'bedrock-agentcore', region '{region}'. Body is JSON-RPC 2.0: first "
        "an 'initialize' request, then 'tools/list', then 'tools/call' with "
        "{name, arguments}. Headers: Content-Type: application/json, Accept: "
        "application/json, text/event-stream. Equivalent to: "
        "botocore.auth.SigV4Auth(creds, 'bedrock-agentcore', region).add_auth("
        "AWSRequest('POST', <mcp_url>, data=<json>, headers=...)). "
        f"MCP URL: {mcp_url}"
    )


def print_verify(evidence: dict) -> None:
    print("\n=== MCP enumeration (tools/list) ===")
    if evidence.get("signed_call_succeeded"):
        for t in evidence.get("tools", []):
            print(f"  - {t['name']}: {t['description'][:72]}...")
        print(f"all 5 expected tools present: {evidence.get('expected_present')}")
        print("\n=== tools/call (one per target) ===")
        for c in evidence.get("calls", []):
            if "error" in c:
                print(f"  {c['tool']} -> ERROR {c['error']}")
            else:
                print(f"  {c['tool']}({c['arguments']}) -> "
                      f"needle '{c['needle']}' found: {c['needle_found']}")
    else:
        print("  signed MCP call NOT possible from this environment.")
        print(f"  error: {evidence.get('error')}")
        print(f"  manual SigV4 path: {evidence.get('manual_sigv4_path')}")


# --- orchestration -----------------------------------------------------------

def provision() -> dict:
    region = _region()
    outputs = _read_outputs()
    lambda_exec_role_arn = outputs.get("lambda_exec_role_arn")
    gateway_role_arn = outputs.get("gateway_role_arn")
    if not lambda_exec_role_arn or not gateway_role_arn:
        sys.exit(
            "FEAT-001 outputs missing: lambda_exec_role_arn / gateway_role_arn "
            "not found in infra/outputs.json. Run infra/02_iam.py first."
        )

    sts = boto3.client("sts", region_name=region)
    account_id = sts.get_caller_identity()["Account"]

    lambda_client = boto3.client("lambda", region_name=region)
    agc = boto3.client("bedrock-agentcore-control", region_name=region)

    # Step 1: package + deploy the three Lambdas.
    lambda_arns: dict = {}
    for svc_key in SERVICE_ORDER:
        zip_bytes = build_zip(svc_key)
        arn = deploy_lambda(lambda_client, svc_key, zip_bytes,
                            lambda_exec_role_arn)
        wait_lambda_active(lambda_client, SERVICES[svc_key]["function_name"])
        lambda_arns[svc_key] = arn

    write_outputs({
        "portfolio_lambda_arn": lambda_arns["portfolio"],
        "market_lambda_arn": lambda_arns["market"],
        "research_lambda_arn": lambda_arns["research"],
    })

    # Step 3: create the gateway FIRST so we have its exact ARN for the policy.
    gw = create_or_get_gateway(agc, gateway_role_arn)
    gateway_id = gw["gatewayId"]
    gateway_arn = gw["gatewayArn"]
    gateway_url = gw["gatewayUrl"]
    write_outputs({"gateway_id": gateway_id, "gateway_mcp_url": gateway_url})
    write_env_value("GATEWAY_MCP_URL", gateway_url)

    # Step 2: add the source-scoped invoke permission (needs the gateway ARN).
    for svc_key in SERVICE_ORDER:
        add_invoke_permission(
            lambda_client, SERVICES[svc_key]["function_name"], account_id,
            gateway_arn,
        )

    # Step 4: create the three Lambda targets with inline tool schemas.
    target_ids: dict = {}
    for svc_key in SERVICE_ORDER:
        target_ids[svc_key] = create_or_get_target(
            agc, gateway_id, svc_key, lambda_arns[svc_key]
        )
    write_outputs({
        "portfolio_target_id": target_ids["portfolio"],
        "market_target_id": target_ids["market"],
        "research_target_id": target_ids["research"],
    })

    print("\nProvisioning complete.")
    print(f"  gateway_id       = {gateway_id}")
    print(f"  gateway_mcp_url  = {gateway_url}")
    for svc_key in SERVICE_ORDER:
        print(f"  {svc_key:<9} lambda = {lambda_arns[svc_key]}")
        print(f"  {svc_key:<9} target = {target_ids[svc_key]}")

    return {"gateway_url": gateway_url, "region": region}


def main() -> None:
    verify_only = "--verify" in sys.argv or "--enumerate" in sys.argv

    if verify_only:
        region = _region()
        outputs = _read_outputs()
        gateway_url = outputs.get("gateway_mcp_url")
        if not gateway_url:
            sys.exit("gateway_mcp_url not in infra/outputs.json; run provision first.")
        evidence = verify_mcp(gateway_url, region)
        print_verify(evidence)
        return

    result = provision()
    # Always run the verification pass at the end of a default run.
    evidence = verify_mcp(result["gateway_url"], result["region"])
    print_verify(evidence)


if __name__ == "__main__":
    main()
