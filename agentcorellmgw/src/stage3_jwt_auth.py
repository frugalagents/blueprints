"""Stage 3: Add CUSTOM_JWT auth to the Gateway using AWS Cognito.

Creates a Cognito User Pool, app client, and test users. Then updates the
Gateway to require JWT auth. Cognito is publicly reachable from AWS — no
tunneling or external hosting needed.
"""

import json
import sys
import time

import boto3
import jwt
import requests
from botocore.exceptions import ClientError

import aws_api
from config import (
    REGION, ACCOUNT_ID,
    save_stage_config, load_stage_config,
)

POOL_NAME = "llm-gateway-users"
CLIENT_NAME = "claude-code-client"
TEST_USERS = [
    {"username": "developer1", "email": "developer1@example.com", "password": "Password123!"},
    {"username": "developer2", "email": "developer2@example.com", "password": "Password123!"},
]


# ---------------------------------------------------------------------------
# Cognito setup
# ---------------------------------------------------------------------------

def create_or_get_user_pool(cognito) -> str:
    """Create Cognito User Pool or return existing ID."""
    pools = cognito.list_user_pools(MaxResults=60)
    existing = next((p for p in pools["UserPools"] if p["Name"] == POOL_NAME), None)
    if existing:
        return existing["Id"]

    resp = cognito.create_user_pool(
        PoolName=POOL_NAME,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
        AutoVerifiedAttributes=["email"],
        Schema=[
            {"Name": "email", "Required": True, "Mutable": True, "AttributeDataType": "String"},
        ],
    )
    return resp["UserPool"]["Id"]


def create_or_get_app_client(cognito, pool_id: str) -> tuple[str, str]:
    """Create app client. Returns (client_id, client_secret)."""
    clients = cognito.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)
    existing = next((c for c in clients["UserPoolClients"] if c["ClientName"] == CLIENT_NAME), None)

    if existing:
        detail = cognito.describe_user_pool_client(
            UserPoolId=pool_id, ClientId=existing["ClientId"]
        )["UserPoolClient"]
        return detail["ClientId"], detail.get("ClientSecret", "")

    resp = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=CLIENT_NAME,
        GenerateSecret=True,
        ExplicitAuthFlows=[
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
            "ALLOW_USER_SRP_AUTH",
        ],
        SupportedIdentityProviders=["COGNITO"],
    )
    client = resp["UserPoolClient"]
    return client["ClientId"], client.get("ClientSecret", "")


def create_test_users(cognito, pool_id: str):
    """Create test users in the pool."""
    for user in TEST_USERS:
        try:
            cognito.admin_create_user(
                UserPoolId=pool_id,
                Username=user["username"],
                UserAttributes=[
                    {"Name": "email", "Value": user["email"]},
                    {"Name": "email_verified", "Value": "true"},
                ],
                MessageAction="SUPPRESS",  # Don't send invite email
                TemporaryPassword=user["password"],
            )
            # Set permanent password (skip forced change)
            cognito.admin_set_user_password(
                UserPoolId=pool_id,
                Username=user["username"],
                Password=user["password"],
                Permanent=True,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "UsernameExistsException":
                pass  # Already exists
            else:
                raise


def get_cognito_token(pool_id: str, client_id: str, client_secret: str,
                      username: str, password: str) -> str:
    """Authenticate a user and return the access token."""
    cognito = boto3.client("cognito-idp", region_name=REGION)

    import hmac, hashlib, base64
    msg = username + client_id
    secret_hash = base64.b64encode(
        hmac.new(client_secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()

    resp = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": username,
            "PASSWORD": password,
            "SECRET_HASH": secret_hash,
        },
    )
    return resp["AuthenticationResult"]["AccessToken"]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup():
    """Create Cognito resources and update Gateway to CUSTOM_JWT."""
    print("=== Stage 3: JWT Auth (Cognito) ===\n")

    stage2 = load_stage_config("stage2")
    gateway_id = stage2["gateway_id"]
    cognito = boto3.client("cognito-idp", region_name=REGION)

    # 1. User Pool
    print("[1/5] Creating Cognito User Pool...")
    pool_id = create_or_get_user_pool(cognito)
    print(f"    ✓ Pool ID: {pool_id}")

    # 2. App Client
    print("[2/5] Creating app client...")
    client_id, client_secret = create_or_get_app_client(cognito, pool_id)
    print(f"    ✓ Client ID: {client_id}")

    # 3. Test Users
    print("[3/5] Creating test users...")
    create_test_users(cognito, pool_id)
    print(f"    ✓ Users created: {[u['username'] for u in TEST_USERS]}")

    # 4. Discovery URL
    discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    print(f"[4/5] Discovery URL: {discovery_url}")

    # Verify reachable
    resp = requests.get(discovery_url, timeout=10)
    issuer = resp.json().get("issuer", "")
    print(f"    ✓ Issuer: {issuer}")

    # 5. Create new gateway with CUSTOM_JWT (can't change auth type on existing)
    print("[5/5] Creating new gateway with CUSTOM_JWT auth...")
    authed_gateway_name = "llm-gateway-authed"

    existing = aws_api.list_gateways()
    authed_gw = next((g for g in existing if g["name"] == authed_gateway_name), None)

    if not authed_gw:
        from config import GATEWAY_ROLE_ARN
        gw_resp = aws_api.create_gateway(
            name=authed_gateway_name,
            role_arn=GATEWAY_ROLE_ARN,
            authorizer_type="CUSTOM_JWT",
            authorizer_config={
                "customJWTAuthorizer": {
                    "discoveryUrl": discovery_url,
                    "allowedClients": [client_id],
                }
            },
            exception_level="DEBUG",
        )
        new_gateway_id = gw_resp["gatewayId"]
    else:
        new_gateway_id = authed_gw["gatewayId"]

    # Wait for READY
    for _ in range(60):
        gw = aws_api.get_gateway(new_gateway_id)
        if gw["status"] == "READY":
            break
        if "FAIL" in gw.get("status", ""):
            raise RuntimeError(f"Gateway failed: {gw.get('statusReasons')}")
        time.sleep(5)

    new_gateway_url = gw["gatewayUrl"]
    new_gateway_arn = gw["gatewayArn"]
    new_inference_url = new_gateway_url.replace("/mcp", "/inference")
    print(f"    ✓ Gateway READY: {new_gateway_id}")

    # Create inference target on new gateway
    print("    Creating bedrock-mantle target on new gateway...")
    targets = aws_api.list_gateway_targets(new_gateway_id)
    target = next((t for t in targets if t["name"] == "bedrock-mantle"), None)

    if not target:
        t_resp = aws_api.create_gateway_target(
            gateway_id=new_gateway_id,
            name="bedrock-mantle",
            target_configuration={
                "inference": {"connector": {"source": {"connectorId": "bedrock-mantle"}}}
            },
            credential_configs=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        target_id = t_resp["targetId"]
    else:
        target_id = target["targetId"]

    # Wait for target READY
    for _ in range(60):
        td = aws_api.get_gateway_target(new_gateway_id, target_id)
        if td["status"] == "READY":
            break
        if "FAIL" in td["status"]:
            raise RuntimeError(f"Target failed: {td.get('statusReasons')}")
        time.sleep(10)
    print(f"    ✓ Target READY: {target_id}")

    # Save config
    save_stage_config("stage3", {
        **stage2,
        "gateway_id": new_gateway_id,
        "gateway_arn": new_gateway_arn,
        "gateway_url": new_gateway_url,
        "inference_url": new_inference_url,
        "target_id": target_id,
        "authorizer_type": "CUSTOM_JWT",
        "cognito_pool_id": pool_id,
        "cognito_client_id": client_id,
        "cognito_client_secret": client_secret,
        "discovery_url": discovery_url,
        "issuer": issuer,
        "original_gateway_id": stage2["gateway_id"],
    })

    print(f"\n=== Stage 3 Setup Complete ===")
    print(f"  Gateway:       {new_gateway_id}")
    print(f"  Inference URL: {new_inference_url}")
    print(f"  Pool ID:       {pool_id}")
    print(f"  Client ID:     {client_id}")
    print(f"  Discovery URL: {discovery_url}")
    print(f"\n  Run: python3 src/stage3_jwt_auth.py test")


# ---------------------------------------------------------------------------
# Token helper
# ---------------------------------------------------------------------------

def _get_token(pool_id: str, client_id: str, client_secret: str,
               username: str, password: str) -> str:
    """Get Cognito access token for a user."""
    import hmac, hashlib, base64

    cognito = boto3.client("cognito-idp", region_name=REGION)
    msg = username + client_id
    secret_hash = base64.b64encode(
        hmac.new(client_secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()

    resp = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": username,
            "PASSWORD": password,
            "SECRET_HASH": secret_hash,
        },
    )
    return resp["AuthenticationResult"]["AccessToken"]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test():
    """Verify Stage 3 — JWT auth gates access correctly."""
    print("=== Stage 3 Verification Tests ===\n")

    config = load_stage_config("stage3")
    inference_url = config["inference_url"]
    model = config["inference_model"]
    pool_id = config["cognito_pool_id"]
    client_id = config["cognito_client_id"]
    client_secret = config["cognito_client_secret"]

    passed, failed = 0, 0

    def check(num: str, name: str, ok: bool, detail: str = ""):
        nonlocal passed, failed
        icon = "✅" if ok else "❌"
        print(f"  {icon} {num} {name}")
        if detail:
            print(f"       {detail}")
        passed += 1 if ok else 0
        failed += 1 if not ok else 0

    def _bearer_request(token: str, body: dict) -> requests.Response:
        url = f"{inference_url}/v1/messages"
        return requests.post(url, json=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        })

    msg_body = {
        "model": model,
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "Say hello in one word."}],
    }

    # 3.1 No token → rejected
    try:
        resp = requests.post(f"{inference_url}/v1/messages",
                             json=msg_body, headers={"Content-Type": "application/json"})
        check("3.1", "No token → rejected", resp.status_code in (401, 403),
              f"HTTP {resp.status_code}")
    except Exception as e:
        check("3.1", "No token → rejected", False, str(e))

    # 3.2 Garbage token → rejected
    try:
        resp = _bearer_request("garbage-not-a-jwt", msg_body)
        check("3.2", "Invalid token → rejected", resp.status_code in (401, 403),
              f"HTTP {resp.status_code}")
    except Exception as e:
        check("3.2", "Invalid token → rejected", False, str(e))

    # 3.3 Wrong-issuer token → rejected
    try:
        fake_token = jwt.encode({"sub": "fake", "iss": "https://evil.com", "exp": int(time.time()) + 3600},
                                "secret", algorithm="HS256")
        resp = _bearer_request(fake_token, msg_body)
        check("3.3", "Wrong-issuer token → rejected", resp.status_code in (401, 403),
              f"HTTP {resp.status_code}")
    except Exception as e:
        check("3.3", "Wrong-issuer token → rejected", False, str(e))

    # 3.4 Valid developer1 token → accepted
    try:
        token = _get_token(pool_id, client_id, client_secret,
                           TEST_USERS[0]["username"], TEST_USERS[0]["password"])
        resp = _bearer_request(token, msg_body)
        if resp.status_code == 200:
            text = resp.json().get("content", [{}])[0].get("text", "")
            check("3.4", "developer1 token → accepted", bool(text),
                  f"Response: {text.strip()}")
        else:
            check("3.4", "developer1 token → accepted", False,
                  f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        check("3.4", "developer1 token → accepted", False, str(e))

    # 3.5 Valid developer2 token → accepted
    try:
        token2 = _get_token(pool_id, client_id, client_secret,
                            TEST_USERS[1]["username"], TEST_USERS[1]["password"])
        resp = _bearer_request(token2, {
            "model": model, "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 50, "messages": [{"role": "user", "content": "What is 1+1?"}],
        })
        if resp.status_code == 200:
            text = resp.json().get("content", [{}])[0].get("text", "")
            check("3.5", "developer2 token → accepted", bool(text),
                  f"Response: {text.strip()[:50]}")
        else:
            check("3.5", "developer2 token → accepted", False,
                  f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        check("3.5", "developer2 token → accepted", False, str(e))

    # 3.6 Tokens have different identities
    try:
        claims1 = jwt.decode(token, options={"verify_signature": False})
        claims2 = jwt.decode(token2, options={"verify_signature": False})
        sub1 = claims1.get("sub", claims1.get("username", ""))
        sub2 = claims2.get("sub", claims2.get("username", ""))
        check("3.6", "Tokens have different identities", sub1 != sub2,
              f"user1={sub1}, user2={sub2}")
    except Exception as e:
        check("3.6", "Tokens have different identities", False, str(e))

    # Summary
    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    if failed:
        print("❌ Stage 3 FAILED")
        sys.exit(1)
    else:
        print("✅ Stage 3 PASSED — proceed to Stage 4")


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------

def revert():
    """Revert gateway back to NONE auth."""
    stage2 = load_stage_config("stage2")
    gateway_id = stage2["gateway_id"]
    gw = aws_api.get_gateway(gateway_id)

    update_body = {
        "name": gw["name"],
        "roleArn": gw["roleArn"],
        "protocolType": gw["protocolType"],
        "exceptionLevel": "DEBUG",
        "authorizerType": "NONE",
    }
    if gw.get("policyEngineConfiguration"):
        update_body["policyEngineConfiguration"] = gw["policyEngineConfiguration"]

    aws_api.update_gateway(gateway_id, update_body)
    for _ in range(30):
        gw = aws_api.get_gateway(gateway_id)
        if gw["status"] == "READY":
            break
        time.sleep(5)
    print(f"✓ Gateway reverted to NONE auth. Status: {gw['status']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if cmd == "setup":
        setup()
    elif cmd == "test":
        test()
    elif cmd == "revert":
        revert()
    else:
        print(f"Usage: python3 {sys.argv[0]} [setup|test|revert]")
