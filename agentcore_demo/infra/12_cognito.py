"""Idempotent provisioner for the Advisor Copilot CLOUD auth layer (Cognito).

Owns the Cognito slice of the cloud deploy (ADDITIVE to the BFF; the local
VITE_DATA_MODE=bff path is untouched). Creates/reuses, in us-east-1:

  1. User Pool `advisor-copilot-users` + app client `advisor-copilot-webapp`
     (USER_PASSWORD + SRP auth flows, no client secret — this is a public SPA
     client). The app client enables ALLOW_USER_PASSWORD_AUTH /
     ALLOW_USER_SRP_AUTH / ALLOW_REFRESH_TOKEN_AUTH only.

  2. Identity Pool `advisor_copilot_identity` wired to the User Pool as a login
     provider (AllowUnauthenticatedIdentities=FALSE), with:
       * authenticated role `advisor-copilot-webapp-role` — trust =
         cognito-identity.amazonaws.com with
         cognito-identity.amazonaws.com:aud = <identityPoolId> and
         ...amr = "authenticated"; ARN-SCOPED least-privilege inline policy (NO
         bedrock-agentcore:* wildcard):
           - bedrock-agentcore:InvokeAgentRuntime on the runtime ARN + endpoint ARN
           - logs:FilterLogEvents/GetLogEvents/DescribeLogStreams on the runtime
             log group + the aws/spans log group
           - cloudwatch:GetMetricStatistics/ListMetrics scoped (namespace
             condition) to AWS/Bedrock/Guardrails, Bedrock-AgentCore/Evaluations,
             ApplicationSignals
           - agent-registry-control list/get/submit/update on the registry ARN
           - bedrock-agentcore:GetPolicy + GetPolicyEngine on the policy-engine ARN
       * unauthenticated role `advisor-copilot-webapp-unauth-role` — a ZERO
         permission role (explicit Deny-all inline policy). Attached only so the
         pool is well-formed; it grants nothing.

  3. ONE seeded user `advisor` created via admin_create_user with a temp password
     RANDOMLY GENERATED at runtime (secrets, with required char classes) in
     FORCE_CHANGE_PASSWORD state. The temp password is printed ONCE to the
     operator console and recorded ONLY in the gitignored .env (COGNITO_SEED_*)
     — NEVER hardcoded, NEVER committed, NEVER a value from chat. On a re-run the
     user already exists and NO new password is generated (idempotent).

Ids (userPoolId, userPoolClientId, identityPoolId, webapp_role_arn, etc.) are
merge-written into the gitignored infra/outputs.json (existing keys preserved).
NO real id / account id / password is written to any tracked file.

Idempotency: every resource is looked up by name first (ListUserPools /
ListIdentityPools / IAM get_role) and only created on absence; policy puts and
set_identity_pool_roles are overwrite-in-place no-ops. A second run creates no
duplicate pool / identity pool / role / user.

Usage:
    AWS_REGION=us-east-1 python infra/12_cognito.py
    AWS_REGION=us-east-1 python infra/12_cognito.py --verify
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGION = "us-east-1"

USER_POOL_NAME = "advisor-copilot-users"
APP_CLIENT_NAME = "advisor-copilot-webapp"
IDENTITY_POOL_NAME = "advisor_copilot_identity"
AUTH_ROLE_NAME = "advisor-copilot-webapp-role"
UNAUTH_ROLE_NAME = "advisor-copilot-webapp-unauth-role"
AUTH_ROLE_POLICY_NAME = "advisor-copilot-webapp-policy"
UNAUTH_ROLE_POLICY_NAME = "advisor-copilot-webapp-unauth-deny"

SEED_USERNAME = "advisor"

PROJECT_TAG = {"Key": "Project", "Value": "advisor-copilot"}

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"
ENV_PATH = REPO_ROOT / ".env"

SCOPED_METRIC_NAMESPACES = [
    "AWS/Bedrock/Guardrails",
    "Bedrock-AgentCore/Evaluations",
    "ApplicationSignals",
]


# --- outputs.json / .env merge helpers (mirror infra/05_runtime.py) ----------


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


def _require_outputs(outputs: dict) -> None:
    missing = [
        k
        for k in ("runtime_arn", "runtime_endpoint_arn", "runtime_id",
                  "registry_arn", "policy_engine_arn")
        if not outputs.get(k)
    ]
    if missing:
        sys.exit(
            "infra/outputs.json missing required backend keys: "
            f"{missing}. Run infra/01-10 first."
        )


# --- 1. User Pool + app client ----------------------------------------------


def _find_user_pool(idp, name: str) -> str | None:
    paginator = idp.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool.get("Name") == name:
                return pool["Id"]
    return None


def ensure_user_pool(idp) -> str:
    """Create-if-absent the User Pool; return its id. Idempotent by name."""
    pool_id = _find_user_pool(idp, USER_POOL_NAME)
    if pool_id:
        print(f"Reusing existing user pool '{USER_POOL_NAME}': {pool_id}")
        return pool_id

    resp = idp.create_user_pool(
        PoolName=USER_POOL_NAME,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 12,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
        AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        AutoVerifiedAttributes=[],
        MfaConfiguration="OFF",
        UserPoolTags={PROJECT_TAG["Key"]: PROJECT_TAG["Value"]},
    )
    pool_id = resp["UserPool"]["Id"]
    print(f"Created user pool '{USER_POOL_NAME}': {pool_id}")
    return pool_id


def _find_app_client(idp, pool_id: str, name: str) -> str | None:
    paginator = idp.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=pool_id, MaxResults=60):
        for client in page.get("UserPoolClients", []):
            if client.get("ClientName") == name:
                return client["ClientId"]
    return None


def ensure_app_client(idp, pool_id: str) -> str:
    """Create-if-absent the public SPA app client (no secret). Idempotent."""
    client_id = _find_app_client(idp, pool_id, APP_CLIENT_NAME)
    explicit_flows = [
        "ALLOW_USER_PASSWORD_AUTH",
        "ALLOW_USER_SRP_AUTH",
        "ALLOW_REFRESH_TOKEN_AUTH",
    ]
    if client_id:
        print(f"Reusing existing app client '{APP_CLIENT_NAME}': {client_id}")
        idp.update_user_pool_client(
            UserPoolId=pool_id,
            ClientId=client_id,
            ClientName=APP_CLIENT_NAME,
            ExplicitAuthFlows=explicit_flows,
            PreventUserExistenceErrors="ENABLED",
        )
        return client_id

    resp = idp.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=APP_CLIENT_NAME,
        GenerateSecret=False,  # public SPA client
        ExplicitAuthFlows=explicit_flows,
        PreventUserExistenceErrors="ENABLED",
    )
    client_id = resp["UserPoolClient"]["ClientId"]
    print(f"Created app client '{APP_CLIENT_NAME}': {client_id}")
    return client_id


# --- 2. Identity Pool --------------------------------------------------------


def _find_identity_pool(cognito_id, name: str) -> str | None:
    paginator = cognito_id.get_paginator("list_identity_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("IdentityPools", []):
            if pool.get("IdentityPoolName") == name:
                return pool["IdentityPoolId"]
    return None


def ensure_identity_pool(cognito_id, region: str, pool_id: str,
                         client_id: str) -> str:
    """Create-if-absent the Identity Pool wired to the User Pool. Idempotent."""
    provider_name = f"cognito-idp.{region}.amazonaws.com/{pool_id}"
    cognito_provider = {
        "ProviderName": provider_name,
        "ClientId": client_id,
        "ServerSideTokenCheck": False,
    }
    identity_pool_id = _find_identity_pool(cognito_id, IDENTITY_POOL_NAME)
    if identity_pool_id:
        print(f"Reusing existing identity pool '{IDENTITY_POOL_NAME}': {identity_pool_id}")
        cognito_id.update_identity_pool(
            IdentityPoolId=identity_pool_id,
            IdentityPoolName=IDENTITY_POOL_NAME,
            AllowUnauthenticatedIdentities=False,
            CognitoIdentityProviders=[cognito_provider],
        )
        return identity_pool_id

    resp = cognito_id.create_identity_pool(
        IdentityPoolName=IDENTITY_POOL_NAME,
        AllowUnauthenticatedIdentities=False,
        CognitoIdentityProviders=[cognito_provider],
        IdentityPoolTags={PROJECT_TAG["Key"]: PROJECT_TAG["Value"]},
    )
    identity_pool_id = resp["IdentityPoolId"]
    print(f"Created identity pool '{IDENTITY_POOL_NAME}': {identity_pool_id}")
    return identity_pool_id


# --- 3. IAM roles (authenticated least-privilege + unauth zero-perm) ---------


def _identity_trust_policy(identity_pool_id: str, authenticated: bool) -> dict:
    """Trust cognito-identity with aud = identity pool id + amr match."""
    amr = "authenticated" if authenticated else "unauthenticated"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": "cognito-identity.amazonaws.com"},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": identity_pool_id
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": amr
                    },
                },
            }
        ],
    }


def _authenticated_permissions_policy(account_id: str, region: str,
                                      outputs: dict) -> dict:
    """ARN-scoped least-privilege policy for the signed-in webapp identity.

    NO bedrock-agentcore:* wildcard: every bedrock-agentcore action is named and
    scoped to a specific resource ARN from outputs.json.
    """
    runtime_arn = outputs["runtime_arn"]
    runtime_endpoint_arn = outputs["runtime_endpoint_arn"]
    runtime_id = outputs["runtime_id"]
    registry_arn = outputs["registry_arn"]
    policy_engine_arn = outputs["policy_engine_arn"]

    runtime_log_group = (
        f"arn:aws:logs:{region}:{account_id}:log-group:"
        f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"
    )
    spans_log_group = f"arn:aws:logs:{region}:{account_id}:log-group:aws/spans"

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeRuntime",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": [runtime_arn, runtime_endpoint_arn, f"{runtime_arn}/*"],
            },
            {
                "Sid": "ReadRuntimeAndSpanLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:FilterLogEvents",
                    "logs:GetLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": [
                    runtime_log_group,
                    f"{runtime_log_group}:*",
                    spans_log_group,
                    f"{spans_log_group}:*",
                ],
            },
            {
                "Sid": "ReadScopedMetrics",
                "Effect": "Allow",
                "Action": ["cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"cloudwatch:namespace": SCOPED_METRIC_NAMESPACES}
                },
            },
            {
                "Sid": "RegistryReadAndApprove",
                "Effect": "Allow",
                "Action": [
                    "agent-registry-control:ListRegistryRecords",
                    "agent-registry-control:GetRegistryRecord",
                    "agent-registry-control:SubmitRegistryRecordForApproval",
                    "agent-registry-control:UpdateRegistryRecordStatus",
                ],
                "Resource": [registry_arn, f"{registry_arn}/*"],
            },
            {
                "Sid": "PolicyRead",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetPolicy",
                    "bedrock-agentcore:GetPolicyEngine",
                ],
                "Resource": [policy_engine_arn, f"{policy_engine_arn}/*"],
            },
        ],
    }


def _unauthenticated_deny_policy() -> dict:
    """Explicit zero-permission policy for the unauthenticated identity."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyEverything",
                "Effect": "Deny",
                "Action": "*",
                "Resource": "*",
            }
        ],
    }


def ensure_role(iam, role_name: str, trust_policy: dict, description: str) -> str:
    """Return the ARN of role_name, creating it (with trust policy) if absent."""
    try:
        resp = iam.get_role(RoleName=role_name)
        print(f"Reusing existing role '{role_name}'")
        iam.update_assume_role_policy(
            RoleName=role_name, PolicyDocument=json.dumps(trust_policy)
        )
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


def ensure_webapp_roles(iam, account_id: str, region: str, outputs: dict,
                        identity_pool_id: str) -> tuple[str, str]:
    """Create/reuse the authenticated + unauthenticated identity-pool roles."""
    auth_arn = ensure_role(
        iam,
        AUTH_ROLE_NAME,
        _identity_trust_policy(identity_pool_id, authenticated=True),
        "Advisor Copilot SPA authenticated role (ARN-scoped least privilege).",
    )
    iam.put_role_policy(
        RoleName=AUTH_ROLE_NAME,
        PolicyName=AUTH_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(
            _authenticated_permissions_policy(account_id, region, outputs)
        ),
    )
    print(f"Put least-privilege inline policy '{AUTH_ROLE_POLICY_NAME}'.")

    unauth_arn = ensure_role(
        iam,
        UNAUTH_ROLE_NAME,
        _identity_trust_policy(identity_pool_id, authenticated=False),
        "Advisor Copilot SPA unauthenticated role (zero permissions).",
    )
    iam.put_role_policy(
        RoleName=UNAUTH_ROLE_NAME,
        PolicyName=UNAUTH_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(_unauthenticated_deny_policy()),
    )
    print(f"Put explicit deny-all inline policy '{UNAUTH_ROLE_POLICY_NAME}'.")
    return auth_arn, unauth_arn


def set_identity_pool_roles(cognito_id, identity_pool_id: str, auth_arn: str,
                            unauth_arn: str) -> None:
    """Attach the auth/unauth roles to the identity pool (overwrite-in-place)."""
    cognito_id.set_identity_pool_roles(
        IdentityPoolId=identity_pool_id,
        Roles={"authenticated": auth_arn, "unauthenticated": unauth_arn},
    )
    print("Set identity-pool authenticated + unauthenticated roles.")


# --- seed user ---------------------------------------------------------------


def _generate_temp_password() -> str:
    """Random temp password satisfying the pool policy (upper/lower/digit/symbol)."""
    symbols = "!@#$%^&*()-_=+"
    alphabet = string.ascii_letters + string.digits + symbols
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(20))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in symbols for c in pw)
        ):
            return pw


def seed_user(idp, pool_id: str) -> None:
    """Create ONE FORCE_CHANGE_PASSWORD user with a runtime-random temp password.

    Idempotent: if the user already exists, no new password is generated or
    printed. The temp password is printed ONCE and recorded ONLY in gitignored
    .env (COGNITO_SEED_USERNAME / COGNITO_SEED_TEMP_PASSWORD).
    """
    try:
        idp.admin_get_user(UserPoolId=pool_id, Username=SEED_USERNAME)
        print(f"Seed user '{SEED_USERNAME}' already exists; leaving unchanged.")
        return
    except idp.exceptions.UserNotFoundException:
        pass

    temp_password = _generate_temp_password()
    idp.admin_create_user(
        UserPoolId=pool_id,
        Username=SEED_USERNAME,
        TemporaryPassword=temp_password,
        MessageAction="SUPPRESS",  # no email; operator shares the temp pw out-of-band
    )
    write_env_value("COGNITO_SEED_USERNAME", SEED_USERNAME)
    write_env_value("COGNITO_SEED_TEMP_PASSWORD", temp_password)
    print(
        "\n================= ONE-TIME SEED CREDENTIALS =================\n"
        f"  username:       {SEED_USERNAME}\n"
        f"  temp password:  {temp_password}\n"
        "  (FORCE_CHANGE_PASSWORD — must be reset on first sign-in)\n"
        "  Recorded ONLY in gitignored .env; NOT in any tracked file.\n"
        "============================================================\n"
    )


# --- --verify ----------------------------------------------------------------


def verify(idp, cognito_id, iam, outputs: dict) -> None:
    """Describe pool/identity-pool/role and assert the invariants; non-mutating."""
    ok = True

    pool_id = outputs.get("cognito_user_pool_id")
    client_id = outputs.get("cognito_user_pool_client_id")
    identity_pool_id = outputs.get("cognito_identity_pool_id")

    if not (pool_id and client_id and identity_pool_id):
        sys.exit("outputs.json missing cognito ids; run the script first.")

    pool = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    print(f"User pool: {pool['Name']} ({pool_id})")

    client = idp.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)[
        "UserPoolClient"
    ]
    print(f"App client: {client['ClientName']} ({client_id}); "
          f"flows={client.get('ExplicitAuthFlows')}")

    ipool = cognito_id.describe_identity_pool(IdentityPoolId=identity_pool_id)
    print(f"Identity pool: {ipool['IdentityPoolName']} ({identity_pool_id}); "
          f"allowUnauth={ipool['AllowUnauthenticatedIdentities']}")
    if ipool["AllowUnauthenticatedIdentities"]:
        print("  !! identity pool allows unauthenticated identities")
        ok = False

    roles = cognito_id.get_identity_pool_roles(IdentityPoolId=identity_pool_id)
    print(f"Identity-pool roles: {roles.get('Roles')}")

    # Authenticated role: NO bedrock-agentcore:* wildcard anywhere.
    auth_policy = iam.get_role_policy(
        RoleName=AUTH_ROLE_NAME, PolicyName=AUTH_ROLE_POLICY_NAME
    )["PolicyDocument"]
    actions = []
    for stmt in auth_policy["Statement"]:
        act = stmt.get("Action", [])
        actions.extend([act] if isinstance(act, str) else act)
    if "bedrock-agentcore:*" in actions:
        print("  !! authenticated role contains bedrock-agentcore:* wildcard")
        ok = False
    else:
        print("  authenticated role has NO bedrock-agentcore:* wildcard (ok)")

    # Unauthenticated role: zero permissions (explicit Deny-all only).
    unauth_policy = iam.get_role_policy(
        RoleName=UNAUTH_ROLE_NAME, PolicyName=UNAUTH_ROLE_POLICY_NAME
    )["PolicyDocument"]
    effects = {s.get("Effect") for s in unauth_policy["Statement"]}
    if effects != {"Deny"}:
        print(f"  !! unauthenticated role is not deny-only (effects={effects})")
        ok = False
    else:
        print("  unauthenticated role is zero-permission (deny-all) (ok)")

    try:
        idp.admin_get_user(UserPoolId=pool_id, Username=SEED_USERNAME)
        print(f"Seed user '{SEED_USERNAME}' present.")
    except idp.exceptions.UserNotFoundException:
        print(f"  !! seed user '{SEED_USERNAME}' missing")
        ok = False

    print("VERIFY OK" if ok else "VERIFY FAILED")
    if not ok:
        sys.exit(1)


# --- orchestration -----------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="Describe + assert invariants; create nothing.")
    args = parser.parse_args()

    region = _region()
    session = boto3.session.Session(region_name=region)
    account_id = session.client("sts").get_caller_identity()["Account"]

    idp = session.client("cognito-idp")
    cognito_id = session.client("cognito-identity")
    iam = session.client("iam")

    outputs = _read_outputs()

    if args.verify:
        verify(idp, cognito_id, iam, outputs)
        return

    _require_outputs(outputs)

    pool_id = ensure_user_pool(idp)
    client_id = ensure_app_client(idp, pool_id)
    identity_pool_id = ensure_identity_pool(cognito_id, region, pool_id, client_id)
    auth_arn, unauth_arn = ensure_webapp_roles(
        iam, account_id, region, outputs, identity_pool_id
    )
    set_identity_pool_roles(cognito_id, identity_pool_id, auth_arn, unauth_arn)
    seed_user(idp, pool_id)

    write_outputs(
        {
            "cognito_user_pool_id": pool_id,
            "cognito_user_pool_client_id": client_id,
            "cognito_identity_pool_id": identity_pool_id,
            "webapp_role_arn": auth_arn,
            "webapp_unauth_role_arn": unauth_arn,
        }
    )
    print(f"\nWrote cognito ids to {OUTPUTS_PATH} (gitignored).")
    print(f"  user_pool_id={pool_id}")
    print(f"  user_pool_client_id={client_id}")
    print(f"  identity_pool_id={identity_pool_id}")
    print(f"  webapp_role_arn={auth_arn}")


if __name__ == "__main__":
    main()
