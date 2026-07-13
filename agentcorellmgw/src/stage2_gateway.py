"""Stage 2: Create AgentCore Gateway with bedrock-mantle inference target.

Guardrail enforcement via Policy Engine on inference targets requires
action-scoped Cedar policies, but inference targets don't register discrete
actions to the schema (unlike MCP targets). Therefore:

- We attach the policy engine in LOG_ONLY mode for observability.
- The permit-all policy ensures requests flow through.
- Guardrail content filtering is applied via the `guardrailIdentifier` param
  on each inference request (client-side enforcement) until the platform
  supports server-side guardrails on inference targets.

This is a known platform limitation documented here for reproducibility.
"""

import json
import sys
import time
import subprocess

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError

import aws_api
from config import (
    REGION, ACCOUNT_ID, GATEWAY_ROLE_ARN, GATEWAY_ROLE_NAME, MODEL_ID,
    save_stage_config, load_stage_config,
)

GATEWAY_NAME = "llm-gateway"
POLICY_ENGINE_NAME = "llm_gateway_policy_engine"
INFERENCE_MODEL = "bedrock-mantle/anthropic.claude-sonnet-5"


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

def ensure_iam_permissions():
    """Ensure the Gateway IAM role has all required permissions."""
    iam = boto3.client("iam")
    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName="BedrockInvokePolicy",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                        "bedrock:ListFoundationModels",
                        "bedrock:ListInferenceProfiles",
                        "bedrock:GetFoundationModel",
                        "bedrock:GetInferenceProfile",
                        "bedrock:InvokeGuardrailChecks",
                    ],
                    "Resource": "*",
                },
                {"Effect": "Allow", "Action": "bedrock-mantle:*", "Resource": "*"},
                {"Effect": "Allow", "Action": "bedrock-agentcore:*", "Resource": "*"},
            ],
        }),
    )


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

def create_or_get_gateway() -> dict:
    """Create gateway or return existing. Waits until READY."""
    existing = aws_api.list_gateways()
    gateway = next((g for g in existing if g["name"] == GATEWAY_NAME), None)

    if not gateway:
        resp = aws_api.create_gateway(
            name=GATEWAY_NAME,
            role_arn=GATEWAY_ROLE_ARN,
            authorizer_type="NONE",
            exception_level="DEBUG",
        )
        gateway_id = resp["gatewayId"]
    else:
        gateway_id = gateway["gatewayId"]

    for _ in range(60):
        details = aws_api.get_gateway(gateway_id)
        if details["status"] == "READY":
            return details
        if details["status"] in ("FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"Gateway in {details['status']} state")
        time.sleep(5)
    raise TimeoutError("Gateway did not reach READY state")


# ---------------------------------------------------------------------------
# Inference target
# ---------------------------------------------------------------------------

def create_or_get_inference_target(gateway_id: str) -> str:
    """Create bedrock-mantle inference target. Handles failed state by recreating."""
    targets = aws_api.list_gateway_targets(gateway_id)
    target = next((t for t in targets if t["name"] == "bedrock-mantle"), None)

    if target:
        details = aws_api.get_gateway_target(gateway_id, target["targetId"])
        if details["status"] == "FAILED":
            aws_api.delete_gateway_target(gateway_id, target["targetId"])
            time.sleep(5)
            target = None
        elif details["status"] == "READY":
            return target["targetId"]

    if not target:
        resp = aws_api.create_gateway_target(
            gateway_id=gateway_id,
            name="bedrock-mantle",
            target_configuration={
                "inference": {"connector": {"source": {"connectorId": "bedrock-mantle"}}}
            },
            credential_configs=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        target_id = resp["targetId"]
    else:
        target_id = target["targetId"]

    for _ in range(60):
        details = aws_api.get_gateway_target(gateway_id, target_id)
        if details["status"] == "READY":
            return target_id
        if "FAIL" in details["status"]:
            raise RuntimeError(f"Target FAILED: {details.get('statusReasons', ['unknown'])[0][:200]}")
        time.sleep(10)
    raise TimeoutError("Target did not reach READY state")


# ---------------------------------------------------------------------------
# Policy Engine (LOG_ONLY mode for observability)
# ---------------------------------------------------------------------------

def create_or_get_policy_engine() -> tuple[str, str]:
    """Create policy engine. Returns (engine_id, engine_arn)."""
    c = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
        resp = c.create_policy_engine(name=POLICY_ENGINE_NAME)
        engine_id = resp["policyEngineId"]
    except ClientError as e:
        if "Conflict" in str(e):
            engines = c.list_policy_engines()
            engine = next(eng for eng in engines["policyEngines"] if eng["name"] == POLICY_ENGINE_NAME)
            engine_id = engine["policyEngineId"]
        else:
            raise

    for _ in range(30):
        details = c.get_policy_engine(policyEngineId=engine_id)
        if details["status"] == "ACTIVE":
            return engine_id, details["policyEngineArn"]
        time.sleep(5)
    raise TimeoutError("Policy engine did not reach ACTIVE state")


def attach_policy_engine_to_gateway(gateway_id: str, engine_arn: str):
    """Attach policy engine in LOG_ONLY mode."""
    gw = aws_api.get_gateway(gateway_id)
    existing_pe = gw.get("policyEngineConfiguration", {})
    if existing_pe.get("arn") == engine_arn:
        return

    aws_api.update_gateway(gateway_id, {
        "name": gw["name"],
        "roleArn": gw["roleArn"],
        "authorizerType": gw["authorizerType"],
        "protocolType": gw["protocolType"],
        "exceptionLevel": "DEBUG",
        "policyEngineConfiguration": {"arn": engine_arn, "mode": "LOG_ONLY"},
    })

    for _ in range(30):
        gw = aws_api.get_gateway(gateway_id)
        if gw["status"] == "READY":
            return
        time.sleep(5)
    raise TimeoutError("Gateway did not return to READY after policy engine attach")


def create_permit_policy(engine_id: str) -> str:
    """Create the permit-all policy so requests can flow."""
    c = boto3.client("bedrock-agentcore-control", region_name=REGION)

    policies = c.list_policies(policyEngineId=engine_id)
    existing = next((p for p in policies.get("policies", []) if p["name"] == "allow_all_traffic"), None)
    if existing:
        return existing["policyId"]

    resp = c.create_policy(
        policyEngineId=engine_id,
        name="allow_all_traffic",
        definition={"cedar": {"statement": "permit (principal, action, resource is AgentCore::Gateway);"}},
        validationMode="IGNORE_ALL_FINDINGS",
    )
    policy_id = resp["policyId"]

    for _ in range(20):
        details = c.get_policy(policyEngineId=engine_id, policyId=policy_id)
        if details["status"] == "ACTIVE":
            return policy_id
        if "FAIL" in details["status"]:
            raise RuntimeError(f"Permit policy failed: {details.get('statusReasons')}")
        time.sleep(3)
    raise TimeoutError("Permit policy did not reach ACTIVE")


# ---------------------------------------------------------------------------
# SigV4 inference helper
# ---------------------------------------------------------------------------

def sigv4_inference_request(inference_url: str, path: str, method: str = "GET",
                            body: dict | None = None) -> requests.Response:
    """Make a SigV4-signed request to the gateway inference endpoint."""
    url = f"{inference_url}{path}"
    data = json.dumps(body) if body else None
    headers = {"Content-Type": "application/json"}

    session = boto3.Session(region_name=REGION)
    creds = session.get_credentials().get_frozen_credentials()
    aws_request = AWSRequest(method=method, url=url, data=data, headers=headers)
    SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(aws_request)

    return requests.request(method, url, data=data, headers=dict(aws_request.headers))


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup():
    """Full Stage 2 setup."""
    print("=== Stage 2: Gateway + Inference Target + Policy Engine ===\n")

    stage1 = load_stage_config("stage1")

    print("[1/5] Ensuring IAM permissions...")
    ensure_iam_permissions()
    print("    ✓ IAM role updated")

    print(f"[2/5] Creating gateway '{GATEWAY_NAME}'...")
    gw = create_or_get_gateway()
    gateway_id = gw["gatewayId"]
    gateway_arn = gw["gatewayArn"]
    gateway_url = gw["gatewayUrl"]
    inference_url = gateway_url.replace("/mcp", "/inference")
    print(f"    ✓ Gateway READY: {gateway_id}")

    print("[3/5] Registering bedrock-mantle inference target...")
    target_id = create_or_get_inference_target(gateway_id)
    print(f"    ✓ Target READY: {target_id}")

    print(f"[4/5] Creating policy engine (LOG_ONLY)...")
    engine_id, engine_arn = create_or_get_policy_engine()
    attach_policy_engine_to_gateway(gateway_id, engine_arn)
    permit_id = create_permit_policy(engine_id)
    print(f"    ✓ Policy engine attached, permit policy: {permit_id}")

    print("[5/5] Saving configuration...")
    save_stage_config("stage2", {
        "gateway_id": gateway_id,
        "gateway_arn": gateway_arn,
        "gateway_url": gateway_url,
        "inference_url": inference_url,
        "target_id": target_id,
        "policy_engine_id": engine_id,
        "policy_engine_arn": engine_arn,
        "permit_policy_id": permit_id,
        "inference_model": INFERENCE_MODEL,
        "guardrail_id": stage1["guardrail_id"],
        "guardrail_version": stage1["guardrail_version"],
    })

    print(f"\n=== Stage 2 Setup Complete ===")
    print(f"  Gateway:       {gateway_id}")
    print(f"  Inference URL: {inference_url}")
    print(f"  Target:        {target_id}")
    print(f"  Policy Engine: {engine_id} (LOG_ONLY)")
    print(f"  Model:         {INFERENCE_MODEL}")
    print(f"\n  Run: python3 src/stage2_gateway.py test")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test():
    """Verify Stage 2."""
    print("=== Stage 2 Verification Tests ===\n")

    config = load_stage_config("stage2")
    inference_url = config["inference_url"]
    gateway_id = config["gateway_id"]
    model = config["inference_model"]

    passed, failed = 0, 0

    def check(num: str, name: str, ok: bool, detail: str = ""):
        nonlocal passed, failed
        icon = "✅" if ok else "❌"
        print(f"  {icon} {num} {name}")
        if detail:
            print(f"       {detail}")
        passed += 1 if ok else 0
        failed += 1 if not ok else 0

    # 2.1 Gateway READY
    try:
        gw = aws_api.get_gateway(gateway_id)
        check("2.1", "Gateway is READY", gw["status"] == "READY", f"ID: {gateway_id}")
    except Exception as e:
        check("2.1", "Gateway is READY", False, str(e))

    # 2.2 Target registered
    try:
        targets = aws_api.list_gateway_targets(gateway_id)
        mantle = next((t for t in targets if t["name"] == "bedrock-mantle"), None)
        check("2.2", "bedrock-mantle target registered", mantle is not None,
              f"Target: {mantle['targetId']}" if mantle else "Not found")
    except Exception as e:
        check("2.2", "bedrock-mantle target registered", False, str(e))

    # 2.3 List models
    try:
        resp = sigv4_inference_request(inference_url, "/v1/models")
        models = resp.json().get("data", [])
        check("2.3", "/v1/models returns models", len(models) > 0, f"{len(models)} model(s)")
    except Exception as e:
        check("2.3", "/v1/models returns models", False, str(e))

    # 2.4 Chat completion
    try:
        resp = sigv4_inference_request(inference_url, "/v1/messages", method="POST", body={
            "model": model,
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "Say hello in one word."}],
        })
        if resp.status_code == 200:
            text = resp.json().get("content", [{}])[0].get("text", "")
            check("2.4", "Chat completion works", bool(text), f"Response: {text.strip()}")
        else:
            check("2.4", "Chat completion works", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        check("2.4", "Chat completion works", False, str(e))

    # 2.5 Policy engine attached
    try:
        gw = aws_api.get_gateway(gateway_id)
        pe = gw.get("policyEngineConfiguration", {})
        check("2.5", "Policy engine attached", bool(pe.get("arn")), f"mode={pe.get('mode')}")
    except Exception as e:
        check("2.5", "Policy engine attached", False, str(e))

    # 2.6 Consistency — second request also works
    try:
        resp = sigv4_inference_request(inference_url, "/v1/messages", method="POST", body={
            "model": model,
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "What is 2+2?"}],
        })
        if resp.status_code == 200:
            text = resp.json().get("content", [{}])[0].get("text", "")
            check("2.6", "Consistent responses", bool(text), f"Response: {text.strip()[:50]}")
        else:
            check("2.6", "Consistent responses", False, f"HTTP {resp.status_code}")
    except Exception as e:
        check("2.6", "Consistent responses", False, str(e))

    # Summary
    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    if failed:
        print("❌ Stage 2 FAILED")
        sys.exit(1)
    else:
        print("✅ Stage 2 PASSED — proceed to Stage 3")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if cmd == "setup":
        setup()
    elif cmd == "test":
        test()
    else:
        print(f"Usage: python3 {sys.argv[0]} [setup|test]")
