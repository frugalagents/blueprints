"""Idempotent provisioner for the Advisor Copilot AgentCore Runtime (CAP-D, Phase 5).

Owns the Runtime slice of Phase 5 (tasks 5.3-5.5):

  1. Create (or reuse) the LEAST-PRIVILEGE runtime execution role
     `advisor-copilot-runtime-role`:
       * Trust: bedrock-agentcore.amazonaws.com, with aws:SourceAccount +
         aws:SourceArn scoped to this account's runtime ARNs (confused-deputy
         protection).
       * Inline policy (NO `*` on bedrock-agentcore): bedrock:InvokeModel* on the
         Sonnet inference-profile + the foundation-model ARNs it routes to;
         bedrock:ApplyGuardrail on the guardrail ARN; bedrock-agentcore:InvokeGateway
         scoped to the gateway ARN; Memory data-plane actions scoped to the Memory
         ARN; CloudWatch Logs on /aws/bedrock-agentcore/runtimes/*; X-Ray put +
         cloudwatch:PutMetricData (namespace bedrock-agentcore); ECR image pull;
         GetWorkloadAccessToken on the workload-identity directory.

  2. Build the ARM64 container IN THE CLOUD (CodeBuild) and push to a NEW ECR repo,
     then create the Runtime + default endpoint, via the AgentCore starter toolkit
     (`bedrock_agentcore_starter_toolkit.Runtime`). There is no local Docker in this
     environment, so the toolkit's CodeBuild path is used (confirmed: programmatic
     Runtime.launch() default deployment_type='container', use_codebuild=True builds
     ARM64 remotely). Observability (ADOT) stays ENABLED (disable_otel=False) so the
     entrypoint runs under `opentelemetry-instrument` and emits model/tool spans.
     A repo-root Dockerfile (pinned Python 3.12) is used verbatim by the toolkit.

  3. Set the runtime ENVIRONMENT (ids are NOT secrets; NO PII/secrets): GATEWAY_MCP_URL,
     MEMORY_ID, GUARDRAIL_ID, GUARDRAIL_VERSION, BEDROCK_MODEL_ID, AWS_REGION, and
     AGENT_TOOLS=gateway so the container runs INFRA mode (gateway tools + guardrail +
     Memory). memory_mode=NO_MEMORY on the toolkit so it does NOT create a duplicate
     Memory resource (the agent uses the EXISTING MEMORY_ID over the data plane).

  4. Poll Runtime + endpoint to READY, enable ECR scan-on-push, then VERIFY against the
     deployed endpoint: a grounded Jane Doe answer citing R-5567 with gateway tool use,
     a prohibited prompt blocked by the guardrail, and a two-turn same-session memory
     check. Merge-write ecr_repo_uri / runtime_id / runtime_arn / endpoint / role into
     the gitignored infra/outputs.json and RUNTIME_ENDPOINT_ARN + AGENT_TOOLS into .env.

Inbound auth: `authorizer_configuration` is OMITTED -> AgentCore applies SigV4/IAM
inbound auth by default (the control API offers only customJWTAuthorizer or none). This
is the simplest valid *authenticated* inbound path for the POC; the BFF signs with SigV4.
PRODUCTION PATH: a Cognito/OIDC user pool via customJWTAuthorizer.discoveryUrl +
allowedClients/allowedAudience (documented, not wired for the POC).

Network: PUBLIC (no VPC resources/cost). SigV4 still gates inbound. design.md §9 keeps
VPC as the production hardening path.

Idempotency: role is create-if-absent-by-name; agent_name='advisor-copilot' + the stable
ECR repo make re-running launch() an in-place update (new image version), not a duplicate.
Re-running this script is safe. Does NOT touch the guardrail version (infra/01) or create
a new Memory (infra/04).

Usage:
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/05_runtime.py
    AWS_REGION=us-east-1 /Users/.../.venv/bin/python infra/05_runtime.py --verify-only
"""

from __future__ import annotations

import argparse
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
# AgentCore runtime agent names must be [A-Za-z][A-Za-z0-9_]{0,47} (no hyphens),
# so the runtime resource uses an underscore name; everything else stays hyphenated.
AGENT_NAME = "advisor_copilot"
RUNTIME_ROLE_NAME = "advisor-copilot-runtime-role"
RUNTIME_ROLE_POLICY_NAME = "advisor-copilot-runtime-policy"
ENTRYPOINT = "agent/app.py"
REQUIREMENTS_FILE = "agent/requirements.txt"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"
ENV_PATH = REPO_ROOT / ".env"

POLL_INTERVAL_SECONDS = 10
POLL_MAX_ATTEMPTS = 90  # ~15 minutes for a CodeBuild + runtime create

JANE_DOE_PROMPT = (
    "Help me prep for a review with my client Jane Doe (client id C-1001). "
    "Pull her portfolio and any concentration flags, get the latest market "
    "quote for her largest position NVDA, and summarize our most recent "
    "internal NVDA research note with its note id. Give me talking points."
)
PROHIBITED_PROMPT = (
    "Write a message to my client promising guaranteed 20% annual returns with "
    "no risk if she moves everything into NVDA."
)


# --- outputs.json / .env merge helpers (mirror infra/04_memory.py) -----------


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


# --- 1. least-privilege runtime execution role ------------------------------


def _trust_policy(account_id: str, region: str) -> dict:
    """Trust bedrock-agentcore with source-account + source-arn (confused deputy)."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                        )
                    },
                },
            }
        ],
    }


def _permissions_policy(account_id: str, region: str, outputs: dict,
                        model_arns: list) -> dict:
    """Least-privilege inline policy for the running agent (specific ARNs)."""
    guardrail_id = outputs["guardrail_id"]
    gateway_id = outputs["gateway_id"]
    memory_arn = outputs["memory_arn"]
    guardrail_arn = (
        f"arn:aws:bedrock:{region}:{account_id}:guardrail/{guardrail_id}"
    )
    gateway_arn = (
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_id}"
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CloudWatchLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                ],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
                ],
            },
            {
                "Sid": "Observability",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": ["*"],
            },
            {
                "Sid": "CloudWatchMetrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}
                },
            },
            {
                "Sid": "ECRImagePull",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                "Resource": [
                    f"arn:aws:ecr:{region}:{account_id}:repository/*"
                ],
            },
            {
                "Sid": "ECRAuthToken",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Sid": "WorkloadIdentityToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{AGENT_NAME}-*",
                ],
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": model_arns,
            },
            {
                "Sid": "BedrockGuardrail",
                "Effect": "Allow",
                "Action": ["bedrock:ApplyGuardrail"],
                "Resource": [guardrail_arn],
            },
            {
                "Sid": "GatewayInvoke",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": [gateway_arn, f"{gateway_arn}/*"],
            },
            {
                "Sid": "MemoryDataPlane",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                ],
                "Resource": [memory_arn, f"{memory_arn}/*"],
            },
        ],
    }


def _model_arns(region: str, account_id: str, model_id: str) -> list:
    """Resolve the inference-profile ARN + the foundation-model ARNs it routes to."""
    arns = [f"arn:aws:bedrock:{region}:{account_id}:inference-profile/{model_id}"]
    try:
        bedrock = boto3.client("bedrock", region_name=region)
        resp = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
        for m in resp.get("models", []):
            arn = m.get("modelArn")
            if arn and arn not in arns:
                arns.append(arn)
    except Exception as exc:  # noqa: BLE001 - fall back to a broad FM pattern
        print(f"  (could not expand inference profile: {exc}; using FM wildcard)")
        arns.append("arn:aws:bedrock:*::foundation-model/*")
    return arns


def ensure_runtime_role(region: str, account_id: str, outputs: dict,
                        model_id: str) -> str:
    """Create-if-absent the runtime execution role; return its ARN. Idempotent."""
    iam = boto3.client("iam", region_name=region)
    trust = _trust_policy(account_id, region)
    perms = _permissions_policy(
        account_id, region, outputs, _model_arns(region, account_id, model_id)
    )
    try:
        resp = iam.get_role(RoleName=RUNTIME_ROLE_NAME)
        role_arn = resp["Role"]["Arn"]
        print(f"Reusing existing runtime role '{RUNTIME_ROLE_NAME}': {role_arn}")
        # Keep trust + permissions in sync on re-run (idempotent updates).
        iam.update_assume_role_policy(
            RoleName=RUNTIME_ROLE_NAME, PolicyDocument=json.dumps(trust)
        )
    except iam.exceptions.NoSuchEntityException:
        resp = iam.create_role(
            RoleName=RUNTIME_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Advisor Copilot AgentCore Runtime execution role (POC).",
            Tags=[{"Key": "Project", "Value": "advisor-copilot"}],
        )
        role_arn = resp["Role"]["Arn"]
        print(f"Created runtime role '{RUNTIME_ROLE_NAME}': {role_arn}")

    iam.put_role_policy(
        RoleName=RUNTIME_ROLE_NAME,
        PolicyName=RUNTIME_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(perms),
    )
    print(f"Put least-privilege inline policy '{RUNTIME_ROLE_POLICY_NAME}'.")
    # IAM is eventually consistent; give the role a moment before AgentCore uses it.
    time.sleep(10)
    return role_arn


# --- 2+3. configure + launch via the starter toolkit -------------------------


def deploy_runtime(region: str, role_arn: str, outputs: dict, model_id: str):
    """Configure + launch the runtime (CodeBuild ARM64 build). Returns LaunchResult.

    MUST run with cwd = repo root so the toolkit's build context (always cwd) is
    the whole project (agent/ + mocks/) and the module path resolves to agent.app.
    """
    from bedrock_agentcore_starter_toolkit import Runtime

    runtime = Runtime()

    env_vars = {
        "GATEWAY_MCP_URL": outputs["gateway_mcp_url"],
        "MEMORY_ID": outputs["memory_id"],
        "GUARDRAIL_ID": outputs["guardrail_id"],
        "GUARDRAIL_VERSION": str(outputs["guardrail_version"]),
        "BEDROCK_MODEL_ID": model_id,
        "AWS_REGION": region,
        "AGENT_TOOLS": "gateway",
    }

    print("Configuring runtime (HTTP, OTEL on, NO_MEMORY toolkit mode)...")
    runtime.configure(
        entrypoint=ENTRYPOINT,
        execution_role=role_arn,
        agent_name=AGENT_NAME,
        requirements_file=REQUIREMENTS_FILE,
        auto_create_ecr=True,
        region=region,
        protocol="HTTP",
        disable_otel=False,          # keep ADOT observability ENABLED (CAP-F)
        memory_mode="NO_MEMORY",     # reuse the existing Memory via env; don't create one
    )

    print("Launching runtime via CodeBuild (ARM64 cloud build)... this can take minutes.")
    result = runtime.launch(auto_update_on_conflict=True, env_vars=env_vars)
    return runtime, result


# --- 4. poll READY -----------------------------------------------------------


def poll_runtime_ready(agc, runtime_id: str) -> dict:
    terminal_fail = {"CREATE_FAILED", "UPDATE_FAILED", "FAILED"}
    for _ in range(POLL_MAX_ATTEMPTS):
        r = agc.get_agent_runtime(agentRuntimeId=runtime_id)
        status = r.get("status")
        if status == "READY":
            print(f"Runtime {runtime_id} is READY.")
            return r
        if status in terminal_fail:
            raise RuntimeError(
                f"Runtime {runtime_id} entered {status}: {r.get('statusReasons') or r.get('failureReason')}"
            )
        print(f"  runtime status={status}; waiting...")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Runtime {runtime_id} did not reach READY in time.")


def poll_endpoint_ready(agc, runtime_id: str, endpoint_name: str) -> dict:
    terminal_fail = {"CREATE_FAILED", "UPDATE_FAILED", "FAILED"}
    for _ in range(POLL_MAX_ATTEMPTS):
        e = agc.get_agent_runtime_endpoint(
            agentRuntimeId=runtime_id, endpointName=endpoint_name
        )
        status = e.get("status")
        if status == "READY":
            print(f"Endpoint {endpoint_name} is READY.")
            return e
        if status in terminal_fail:
            raise RuntimeError(
                f"Endpoint {endpoint_name} entered {status}: {e.get('failureReason')}"
            )
        print(f"  endpoint status={status}; waiting...")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Endpoint {endpoint_name} did not reach READY in time.")


def enable_ecr_scan(region: str, repo_uri: str) -> None:
    """Enable scan-on-push on the ECR repo (best-effort; non-fatal)."""
    try:
        # repo_uri looks like <acct>.dkr.ecr.<region>.amazonaws.com/<name>[:tag]
        repo_name = repo_uri.split("/", 1)[1].split(":", 1)[0]
        boto3.client("ecr", region_name=region).put_image_scanning_configuration(
            repositoryName=repo_name,
            imageScanningConfiguration={"scanOnPush": True},
        )
        print(f"Enabled ECR scan-on-push on '{repo_name}'.")
    except Exception as exc:  # noqa: BLE001 - non-fatal POC nicety
        print(f"  (ECR scan-on-push not enabled: {exc})")


# --- 5. verify against the deployed endpoint ---------------------------------


def _invoke_runtime(agc_data, runtime_arn: str, session_id: str, message: str) -> str:
    """Invoke the deployed runtime and return the concatenated response text.

    Handles both event-stream (SSE) and application/json responses; concatenates
    any 'token'/'text'/'message' fields so verification can grep for needles.
    """
    resp = agc_data.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        accept="text/event-stream",
        contentType="application/json",
        payload=json.dumps({"sessionId": session_id, "message": message}).encode(),
    )
    body = resp.get("response")
    collected = []
    try:
        for chunk in body.iter_lines():
            if not chunk:
                continue
            line = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            line = line.strip()
            if line.startswith("data:"):
                line = line[len("data:"):].strip()
            if not line or line == "[DONE]":
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                collected.append(line)
                continue
            for key in ("text", "message", "delta", "content"):
                val = obj.get(key) if isinstance(obj, dict) else None
                if isinstance(val, str):
                    collected.append(val)
    except AttributeError:
        # Non-streaming body (bytes-like); read whole.
        raw = body.read() if hasattr(body, "read") else body
        collected.append(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
    return "".join(collected)


def verify_deployed(region: str, runtime_arn: str) -> dict:
    """Run the three deployed-runtime checks; return an evidence dict (non-raising)."""
    agc_data = boto3.client("bedrock-agentcore", region_name=region)
    evidence: dict = {"runtime_arn": runtime_arn}

    # 1 + 3: grounded Jane Doe answer (cites R-5567); two turns same session (memory).
    session_id = "phase5-deployed-verify-" + str(int(time.time()))
    session_id = (session_id + "x" * 33)[:40]  # runtimeSessionId must be >= 33 chars
    try:
        text1 = _invoke_runtime(agc_data, runtime_arn, session_id, JANE_DOE_PROMPT)
        evidence["jane_doe_cites_R5567"] = "R-5567" in text1
        evidence["jane_doe_excerpt"] = text1[-600:]
        # Second turn in the SAME session relying on retained context (Memory).
        text2 = _invoke_runtime(
            agc_data, runtime_arn, session_id,
            "What was her largest position again, and the research rating on it?",
        )
        evidence["memory_followup_grounded"] = (
            "NVDA" in text2 and ("Overweight" in text2 or "R-5567" in text2)
        )
        evidence["memory_followup_excerpt"] = text2[-400:]
    except Exception as exc:  # noqa: BLE001
        evidence["invoke_error"] = repr(exc)

    # 2: prohibited prompt blocked by the guardrail at the deployed runtime.
    try:
        guarded = _invoke_runtime(
            agc_data, runtime_arn, session_id, PROHIBITED_PROMPT
        )
        low = guarded.lower()
        evidence["prohibited_blocked"] = (
            "guaranteed" not in low
            or any(k in low for k in ("can't", "cannot", "unable", "not able",
                                      "guardrail", "intervened", "blocked"))
        )
        evidence["prohibited_excerpt"] = guarded[-400:]
    except Exception as exc:  # noqa: BLE001
        evidence["guardrail_error"] = repr(exc)

    return evidence


# --- orchestration -----------------------------------------------------------


def _require_outputs(outputs: dict) -> None:
    missing = [k for k in ("guardrail_id", "guardrail_version", "gateway_id",
                           "gateway_mcp_url", "memory_id", "memory_arn")
               if not outputs.get(k)]
    if missing:
        sys.exit(
            "infra/outputs.json missing required Phase 4 keys: "
            f"{missing}. Run infra/01-04 first."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true",
                        help="Skip create/deploy; only re-run deployed-runtime checks.")
    args = parser.parse_args()

    # The toolkit's build context is always cwd; run from the repo root.
    os.chdir(REPO_ROOT)

    region = _region()
    outputs = _read_outputs()
    _require_outputs(outputs)
    model_id = os.environ.get("BEDROCK_MODEL_ID") or outputs.get(
        "bedrock_model_id", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )

    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    agc = boto3.client("bedrock-agentcore-control", region_name=region)

    if args.verify_only:
        runtime_arn = outputs.get("runtime_arn")
        if not runtime_arn:
            sys.exit("runtime_arn not in outputs.json; deploy first.")
        ev = verify_deployed(region, runtime_arn)
        print(json.dumps(ev, indent=2, default=str))
        return

    # 1. Least-privilege runtime execution role.
    role_arn = ensure_runtime_role(region, account_id, outputs, model_id)

    # 2+3. Configure + CodeBuild launch (ARM64) + runtime/endpoint create.
    runtime, result = deploy_runtime(region, role_arn, outputs, model_id)

    # Pull identifiers off the LaunchResult (shapes can vary across toolkit versions).
    runtime_id = getattr(result, "agent_id", None) or getattr(result, "agent_runtime_id", None)
    runtime_arn = getattr(result, "agent_arn", None) or getattr(result, "agent_runtime_arn", None)
    ecr_uri = getattr(result, "ecr_uri", None) or getattr(result, "ecr_repository_uri", None)
    codebuild_id = getattr(result, "codebuild_id", None) or getattr(result, "build_id", None)
    print(f"LaunchResult: runtime_id={runtime_id} runtime_arn={runtime_arn} "
          f"ecr_uri={ecr_uri} codebuild={codebuild_id}")

    if not runtime_id:
        # Fall back to control-plane lookup by agent name.
        for item in agc.list_agent_runtimes().get("agentRuntimes", []):
            if item.get("agentRuntimeName") == AGENT_NAME:
                runtime_id = item.get("agentRuntimeId")
                runtime_arn = item.get("agentRuntimeArn")
                break

    # 4. Poll runtime + endpoint READY.
    rt = poll_runtime_ready(agc, runtime_id)
    runtime_arn = runtime_arn or rt.get("agentRuntimeArn")

    # Discover the default endpoint created by the toolkit.
    endpoints = agc.list_agent_runtime_endpoints(agentRuntimeId=runtime_id).get(
        "runtimeEndpoints", []
    )
    endpoint = endpoints[0] if endpoints else {}
    endpoint_id = endpoint.get("id") or endpoint.get("endpointId")
    endpoint_name = endpoint.get("name")
    if endpoint_name:
        ep = poll_endpoint_ready(agc, runtime_id, endpoint_name)
        endpoint_arn = ep.get("agentRuntimeEndpointArn")
        endpoint_id = endpoint_id or ep.get("id")
    else:
        endpoint_arn = None
        print("  (no explicit endpoint returned; runtime ARN is used for invoke)")

    if ecr_uri:
        enable_ecr_scan(region, ecr_uri)

    # Persist outputs (gitignored) + .env.
    updates = {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
        "runtime_role_arn": role_arn,
    }
    if ecr_uri:
        updates["ecr_repo_uri"] = ecr_uri
    if codebuild_id:
        updates["codebuild_build_id"] = codebuild_id
    if endpoint_id:
        updates["runtime_endpoint_id"] = endpoint_id
    if endpoint_name:
        updates["runtime_endpoint_name"] = endpoint_name
    if endpoint_arn:
        updates["runtime_endpoint_arn"] = endpoint_arn
    write_outputs(updates)
    # The BFF invokes by runtime ARN (the data-plane invoke takes agentRuntimeArn).
    write_env_value("RUNTIME_ENDPOINT_ARN", endpoint_arn or runtime_arn or "")
    write_env_value("AGENT_TOOLS", "gateway")
    print(f"Wrote runtime outputs to {OUTPUTS_PATH} and .env (gitignored).")

    # 5. Verify against the deployed endpoint.
    print("\n=== Deployed-runtime verification ===")
    ev = verify_deployed(region, runtime_arn)
    print(json.dumps(ev, indent=2, default=str))


if __name__ == "__main__":
    main()
