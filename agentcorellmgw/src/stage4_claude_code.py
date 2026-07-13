"""Stage 4: Configure and verify Claude Code integration with the Gateway.

Claude Code uses ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN env vars to route
through the Gateway. This stage:
  1. Generates a helper script that sets up the env for developers
  2. Verifies the Gateway endpoint responds to Anthropic Messages API format
  3. Tests token refresh workflow
"""

import json
import os
import sys
import time
from pathlib import Path

import boto3
import requests

from config import REGION, PROJECT_ROOT, save_stage_config, load_stage_config
from stage3_jwt_auth import _get_token, TEST_USERS


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup():
    """Generate developer-facing env config and verify endpoint compatibility."""
    print("=== Stage 4: Claude Code Integration ===\n")

    stage3 = load_stage_config("stage3")
    inference_url = stage3["inference_url"]
    pool_id = stage3["cognito_pool_id"]
    client_id = stage3["cognito_client_id"]
    client_secret = stage3["cognito_client_secret"]

    # 1. Generate env.example
    print("[1/3] Generating client config files...")
    client_dir = PROJECT_ROOT / "client"
    client_dir.mkdir(exist_ok=True)

    env_example = client_dir / "env.example"
    env_example.write_text(f"""# Claude Code Gateway Configuration
# Source this file: source client/env.example

# Gateway inference endpoint (Anthropic Messages API compatible)
export ANTHROPIC_BASE_URL="{inference_url}"

# Per-user OAuth token from Cognito (replace with your actual token)
export ANTHROPIC_AUTH_TOKEN="<YOUR_COGNITO_ACCESS_TOKEN>"

# To get a token, run:
#   python3 src/stage4_claude_code.py get-token --user developer1
""")
    print(f"    ✓ Created client/env.example")

    # 2. Generate token helper script
    get_token_script = client_dir / "get-token.py"
    script_content = (
        '#!/usr/bin/env python3\n'
        '"""Get a Cognito access token for use with Claude Code."""\n\n'
        'import argparse\nimport hmac\nimport hashlib\nimport base64\nimport boto3\n\n'
        f'REGION = "{REGION}"\n'
        f'POOL_ID = "{pool_id}"\n'
        f'CLIENT_ID = "{client_id}"\n'
        f'CLIENT_SECRET = "{client_secret}"\n'
        f'INFERENCE_URL = "{inference_url}"\n\n\n'
        'def get_token(username: str, password: str) -> str:\n'
        '    cognito = boto3.client("cognito-idp", region_name=REGION)\n'
        '    msg = username + CLIENT_ID\n'
        '    secret_hash = base64.b64encode(\n'
        '        hmac.new(CLIENT_SECRET.encode(), msg.encode(), hashlib.sha256).digest()\n'
        '    ).decode()\n\n'
        '    resp = cognito.initiate_auth(\n'
        '        ClientId=CLIENT_ID,\n'
        '        AuthFlow="USER_PASSWORD_AUTH",\n'
        '        AuthParameters={\n'
        '            "USERNAME": username,\n'
        '            "PASSWORD": password,\n'
        '            "SECRET_HASH": secret_hash,\n'
        '        },\n'
        '    )\n'
        '    return resp["AuthenticationResult"]["AccessToken"]\n\n\n'
        'if __name__ == "__main__":\n'
        '    parser = argparse.ArgumentParser(description="Get Cognito token for Claude Code")\n'
        '    parser.add_argument("--user", default="developer1")\n'
        '    parser.add_argument("--password", default="Password123!")\n'
        '    args = parser.parse_args()\n\n'
        '    token = get_token(args.user, args.password)\n'
        '    print(f"\\nexport ANTHROPIC_BASE_URL=\\"{INFERENCE_URL}\\\"")\n'
        '    print(f"export ANTHROPIC_AUTH_TOKEN=\\"{token}\\\"")\n'
        '    print("\\n# Then run: claude")\n'
    )
    get_token_script.write_text(script_content)
    os.chmod(get_token_script, 0o755)
    print(f"    ✓ Created client/get-token.py")

    # 3. Verify endpoint responds like Anthropic Messages API
    print("\n[2/3] Verifying endpoint Anthropic API compatibility...")
    token = _get_token(pool_id, client_id, client_secret,
                       TEST_USERS[0]["username"], TEST_USERS[0]["password"])

    # Test /v1/messages (the core endpoint Claude Code uses)
    resp = requests.post(
        f"{inference_url}/v1/messages",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={
            "model": stage3["inference_model"],
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "Reply with just the word 'working'."}],
        },
    )

    if resp.status_code == 200:
        body = resp.json()
        # Verify response shape matches Anthropic Messages API
        has_content = "content" in body
        has_model = "model" in body
        has_usage = "usage" in body
        print(f"    ✓ /v1/messages returns valid response")
        print(f"      content: {has_content}, model: {has_model}, usage: {has_usage}")
        print(f"      Response: {body['content'][0]['text'].strip()}")
    else:
        print(f"    ✗ /v1/messages returned HTTP {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)

    # 3. Save config
    print("\n[3/3] Saving configuration...")
    save_stage_config("stage4", {
        **stage3,
        "anthropic_base_url": inference_url,
    })

    print(f"\n=== Stage 4 Setup Complete ===")
    print(f"  ANTHROPIC_BASE_URL: {inference_url}")
    print(f"  Token helper: client/get-token.py")
    print(f"\n  To use with Claude Code:")
    print(f"    python3 client/get-token.py --user developer1")
    print(f"    # then export the vars and run 'claude'")
    print(f"\n  Run: python3 src/stage4_claude_code.py test")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test():
    """Verify Stage 4 — Claude Code compatibility."""
    print("=== Stage 4 Verification Tests ===\n")

    config = load_stage_config("stage4")
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

    # Get a fresh token
    token = _get_token(pool_id, client_id, client_secret,
                       TEST_USERS[0]["username"], TEST_USERS[0]["password"])

    def _claude_request(messages: list, max_tokens: int = 50) -> requests.Response:
        return requests.post(
            f"{inference_url}/v1/messages",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={
                "model": model,
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": messages,
            },
        )

    # 4.1 Base URL serves the messages endpoint
    try:
        resp = _claude_request([{"role": "user", "content": "Say 'test' and nothing else."}])
        check("4.1", "ANTHROPIC_BASE_URL + /v1/messages works", resp.status_code == 200,
              f"HTTP {resp.status_code}")
    except Exception as e:
        check("4.1", "ANTHROPIC_BASE_URL + /v1/messages works", False, str(e))

    # 4.2 Response has Anthropic Messages API shape
    try:
        body = resp.json()
        has_fields = all(k in body for k in ["content", "model", "role", "stop_reason"])
        check("4.2", "Response shape matches Anthropic API", has_fields,
              f"Keys: {list(body.keys())}")
    except Exception as e:
        check("4.2", "Response shape matches Anthropic API", False, str(e))

    # 4.3 Response includes usage (tokens)
    try:
        usage = body.get("usage", {})
        has_tokens = "input_tokens" in usage and "output_tokens" in usage
        check("4.3", "Response includes token usage", has_tokens,
              f"input={usage.get('input_tokens')}, output={usage.get('output_tokens')}")
    except Exception as e:
        check("4.3", "Response includes token usage", False, str(e))

    # 4.4 Multi-turn conversation works
    try:
        resp = _claude_request([
            {"role": "user", "content": "Remember the number 42."},
            {"role": "assistant", "content": "I'll remember 42."},
            {"role": "user", "content": "What number did I say? Reply with just the number."},
        ])
        text = resp.json().get("content", [{}])[0].get("text", "")
        has_42 = "42" in text
        check("4.4", "Multi-turn conversation works", resp.status_code == 200 and has_42,
              f"Response: {text.strip()[:50]}")
    except Exception as e:
        check("4.4", "Multi-turn conversation works", False, str(e))

    # 4.5 Token refresh — new token also works
    try:
        new_token = _get_token(pool_id, client_id, client_secret,
                               TEST_USERS[0]["username"], TEST_USERS[0]["password"])
        resp = requests.post(
            f"{inference_url}/v1/messages",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {new_token}"},
            json={"model": model, "anthropic_version": "bedrock-2023-05-31",
                  "max_tokens": 20, "messages": [{"role": "user", "content": "Say yes."}]},
        )
        check("4.5", "Refreshed token works", resp.status_code == 200,
              f"HTTP {resp.status_code}")
    except Exception as e:
        check("4.5", "Refreshed token works", False, str(e))

    # 4.6 client/env.example file exists
    env_file = PROJECT_ROOT / "client" / "env.example"
    check("4.6", "client/env.example exists", env_file.exists(),
          str(env_file) if env_file.exists() else "Missing")

    # Summary
    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    if failed:
        print("❌ Stage 4 FAILED")
        sys.exit(1)
    else:
        print("✅ Stage 4 PASSED — proceed to Stage 5")


# ---------------------------------------------------------------------------
# Helper: get-token CLI
# ---------------------------------------------------------------------------

def get_token_cli():
    """Print a fresh token for a user."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="developer1")
    parser.add_argument("--password", default="Password123!")
    args, _ = parser.parse_known_args(sys.argv[2:])

    config = load_stage_config("stage3")
    token = _get_token(
        config["cognito_pool_id"],
        config["cognito_client_id"],
        config["cognito_client_secret"],
        args.user, args.password,
    )
    print(f"\nexport ANTHROPIC_BASE_URL=\"{config['inference_url']}\"")
    print(f"export ANTHROPIC_AUTH_TOKEN=\"{token}\"")
    print(f"\n# Then run: claude")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if cmd == "setup":
        setup()
    elif cmd == "test":
        test()
    elif cmd == "get-token":
        get_token_cli()
    else:
        print(f"Usage: python3 {sys.argv[0]} [setup|test|get-token]")
