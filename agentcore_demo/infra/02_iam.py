"""Idempotent provisioner for Phase 4 least-privilege IAM roles.

Creates (or reuses) the two IAM roles the AgentCore Gateway + mock Lambdas need:

  1. advisor-copilot-lambda-exec   — Lambda execution role, basic CloudWatch
     Logs only (AWS-managed AWSLambdaBasicExecutionRole). The three mock Lambdas
     make no downstream AWS calls, so no further permissions are granted.

  2. advisor-copilot-gateway-role  — AgentCore Gateway service role. Trusts
     bedrock-agentcore.amazonaws.com with confused-deputy protection
     (aws:SourceAccount + aws:SourceArn on the gateway/* ARN) and an INLINE
     policy granting lambda:InvokeFunction on exactly the three mock-Lambda
     function ARNs (never Resource "*"). Because Phase 4 uses the Lambda +
     inline-toolSchema gateway-target transport (NOT OpenAPI-schema-in-S3),
     this role deliberately includes NO s3:GetObject grant.

RUNTIME ROLE — DEFERRED (NOT created here):
  The Phase 5 Runtime execution role (advisor-copilot-runtime-role) is
  intentionally NOT created by this script. Phase 5 owns it and it will need
  bedrock:InvokeModel for the agent model, SigV4 (bedrock-agentcore) access to
  the Gateway MCP endpoint, Memory read/write via the data-plane
  bedrock-agentcore client, and ADOT/CloudWatch telemetry permissions. Creating
  it now would grant access to resources (Runtime/Memory/Gateway) that either do
  not exist yet or whose ARNs are not final, so it is deferred by design.

Idempotency: each role is looked up with get_role first; create_role is only
called on NoSuchEntity. attach_role_policy and put_role_policy are themselves
idempotent (repeating them is a no-op), so a second run reuses both roles with
no duplicate and no error.

The role ARNs are merge-written into gitignored infra/outputs.json (existing
keys such as guardrail_id/guardrail_version/region are preserved). FEAT-002
reads lambda_exec_role_arn and gateway_role_arn from there.

Usage:

    AWS_REGION=us-east-1 python infra/02_iam.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGION = "us-east-1"

LAMBDA_EXEC_ROLE_NAME = "advisor-copilot-lambda-exec"
GATEWAY_ROLE_NAME = "advisor-copilot-gateway-role"
GATEWAY_INLINE_POLICY_NAME = "advisor-copilot-gateway-invoke-lambda"

BASIC_EXECUTION_POLICY_ARN = (
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
)

# Names must match the functions FEAT-002 (infra/03_gateway.py) will create.
LAMBDA_FUNCTION_NAMES = (
    "advisor-copilot-portfolio",
    "advisor-copilot-market",
    "advisor-copilot-research",
)

PROJECT_TAG = {"Key": "Project", "Value": "advisor-copilot"}

OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"


def _region() -> str:
    return os.environ.get("AWS_REGION") or DEFAULT_REGION


def _account_id(session: boto3.session.Session) -> str:
    """Resolve the account id at runtime; never hardcoded."""
    return session.client("sts").get_caller_identity()["Account"]


def _lambda_function_arns(region: str, account_id: str) -> list[str]:
    return [
        f"arn:aws:lambda:{region}:{account_id}:function:{name}"
        for name in LAMBDA_FUNCTION_NAMES
    ]


def _lambda_trust_policy() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def _gateway_trust_policy(region: str, account_id: str) -> dict:
    """Trust bedrock-agentcore with confused-deputy source conditions."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"
                        )
                    },
                },
            }
        ],
    }


def _gateway_inline_policy(function_arns: list[str]) -> dict:
    """lambda:InvokeFunction scoped to exactly the three function ARNs."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeMockLambdas",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": function_arns,
            }
        ],
    }


def ensure_role(iam, role_name: str, trust_policy: dict, description: str) -> str:
    """Return the ARN of role_name, creating it (with trust policy) if absent."""
    try:
        resp = iam.get_role(RoleName=role_name)
        print(f"Reusing existing role '{role_name}'")
        return resp["Role"]["Arn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise

    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description=description,
        Tags=[PROJECT_TAG],
    )
    print(f"Created role '{role_name}'")
    return resp["Role"]["Arn"]


def ensure_lambda_exec_role(iam) -> str:
    """Create/reuse the Lambda execution role with basic-logs policy only."""
    arn = ensure_role(
        iam,
        LAMBDA_EXEC_ROLE_NAME,
        _lambda_trust_policy(),
        "Advisor Copilot mock-Lambda execution role (basic CloudWatch Logs only).",
    )
    # attach_role_policy is idempotent: re-attaching the same managed policy is a no-op.
    iam.attach_role_policy(
        RoleName=LAMBDA_EXEC_ROLE_NAME,
        PolicyArn=BASIC_EXECUTION_POLICY_ARN,
    )
    print(f"Ensured managed policy AWSLambdaBasicExecutionRole on '{LAMBDA_EXEC_ROLE_NAME}'")
    return arn


def ensure_gateway_role(iam, region: str, account_id: str) -> str:
    """Create/reuse the Gateway service role with the scoped inline invoke policy."""
    arn = ensure_role(
        iam,
        GATEWAY_ROLE_NAME,
        _gateway_trust_policy(region, account_id),
        (
            "Advisor Copilot AgentCore Gateway service role: invokes the three "
            "mock Lambdas via Lambda targets (least privilege, source-scoped trust)."
        ),
    )
    function_arns = _lambda_function_arns(region, account_id)
    # put_role_policy is idempotent by policy name: it overwrites in place.
    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName=GATEWAY_INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(_gateway_inline_policy(function_arns)),
    )
    print(
        f"Ensured inline policy '{GATEWAY_INLINE_POLICY_NAME}' on "
        f"'{GATEWAY_ROLE_NAME}' (lambda:InvokeFunction on {len(function_arns)} ARNs)"
    )
    return arn


def write_outputs(lambda_exec_role_arn: str, gateway_role_arn: str) -> None:
    """Merge the two role ARNs into infra/outputs.json, preserving existing keys."""
    data: dict = {}
    if OUTPUTS_PATH.exists():
        try:
            data = json.loads(OUTPUTS_PATH.read_text())
        except (ValueError, OSError):
            data = {}
    data.update(
        {
            "lambda_exec_role_arn": lambda_exec_role_arn,
            "gateway_role_arn": gateway_role_arn,
        }
    )
    OUTPUTS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def main() -> None:
    region = _region()
    session = boto3.session.Session(region_name=region)
    account_id = _account_id(session)
    iam = session.client("iam")

    lambda_exec_role_arn = ensure_lambda_exec_role(iam)
    gateway_role_arn = ensure_gateway_role(iam, region, account_id)

    # Runtime role (advisor-copilot-runtime-role) is DEFERRED to Phase 5 — see
    # module docstring. It is intentionally NOT created here.

    write_outputs(lambda_exec_role_arn, gateway_role_arn)
    print(f"Wrote {OUTPUTS_PATH}")
    print(f"lambda_exec_role_arn={lambda_exec_role_arn}")
    print(f"gateway_role_arn={gateway_role_arn}")


if __name__ == "__main__":
    main()
