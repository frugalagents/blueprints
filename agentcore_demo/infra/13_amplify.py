"""Idempotent provisioner for the Advisor Copilot SPA on Amplify Hosting.

Owns the Amplify slice of the cloud deploy (ADDITIVE to the BFF; the local
VITE_DATA_MODE=bff path is untouched). This is the SANCTIONED amplifyapp.com SPA
+ Cognito pattern — NO raw public S3 website, open ALB, or unauthenticated
endpoint. The SPA itself gates on the Cognito login (FEAT-001/002), and the
browser calls AWS directly under the Identity Pool SigV4 identity (FEAT-002).

What this script does, idempotently (create-or-reuse by name):

  1. Create/reuse the Amplify app `advisor-copilot-ui` (platform WEB) with:
       * the build spec from the repo-root amplify.yml,
       * a SPA rewrite custom rule (serve index.html for client-side routes),
       * build-time environment variables = VITE_DATA_MODE=direct + the
         Cognito/runtime/registry/log-group/policy/guardrail ids read from the
         gitignored infra/outputs.json (ids are NOT secrets; still never written
         to a tracked file — they live only in Amplify env + outputs.json).
  2. Create/reuse a branch `main`.
  3. Build the SPA locally with the SAME VITE_* env injected (npm ci + npm run
     build under ui/), then deploy the built artifact (ui/dist) via Amplify
     MANUAL (zip) deploy: create_deployment -> upload the zip to the returned
     presigned URL -> start_deployment -> poll the job to SUCCEED. (Manual deploy
     avoids wiring a Git provider / OAuth token for the POC; amplify.yml documents
     the equivalent Amplify-managed build for a connected branch.)
  4. Capture amplify_app_id, amplify_branch, amplify_default_domain
     (https://<branch>.<appId>.amplifyapp.com) into gitignored infra/outputs.json.

Idempotency: the app is looked up by name via list_apps; the branch via
get_branch; env vars + build spec + custom rules are overwritten in place on
re-run (update_app). A second run reuses the app + branch and redeploys the
current artifact (no duplicate app).

Usage:
    AWS_REGION=us-east-1 python infra/13_amplify.py
    AWS_REGION=us-east-1 python infra/13_amplify.py --verify
"""

from __future__ import annotations

import argparse
import io
import json
import os
import ssl
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import boto3
import certifi
from botocore.exceptions import ClientError

DEFAULT_REGION = "us-east-1"

APP_NAME = "advisor-copilot-ui"
BRANCH_NAME = "main"

PROJECT_TAG_KEY = "Project"
PROJECT_TAG_VALUE = "advisor-copilot"

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "ui"
UI_DIST = UI_DIR / "dist"
AMPLIFY_YML = REPO_ROOT / "amplify.yml"
OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"

POLL_INTERVAL_SECONDS = 10
POLL_MAX_ATTEMPTS = 60  # ~10 minutes for a deploy job

# SPA rewrite: serve index.html for any route that is not a real asset so
# client-side routing works (standard Amplify SPA rule).
SPA_REWRITE_RULE = {
    "source": "</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json)$)([^.]+$)/>",
    "target": "/index.html",
    "status": "200",
}


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
    missing = [
        k
        for k in (
            "cognito_user_pool_id",
            "cognito_user_pool_client_id",
            "cognito_identity_pool_id",
            "runtime_arn",
            "runtime_endpoint_arn",
            "registry_id",
            "gateway_id",
            "runtime_id",
            "guardrail_id",
            "guardrail_version",
            "policy_engine_id",
            "policy_id",
            "otel_service_name",
        )
        if not outputs.get(k)
    ]
    if missing:
        sys.exit(
            "infra/outputs.json missing required keys: "
            f"{missing}. Run infra/12_cognito.py (and the backend scripts) first."
        )


def _vite_env(region: str, outputs: dict) -> dict:
    """Build the VITE_* env the direct DataClient (FEAT-002) reads, from outputs."""
    runtime_id = outputs["runtime_id"]
    guardrail_arn = (
        f"arn:aws:bedrock:{region}:"
        f"{boto3.client('sts', region_name=region).get_caller_identity()['Account']}"
        f":guardrail/{outputs['guardrail_id']}"
    )
    return {
        "VITE_DATA_MODE": "direct",
        "VITE_AWS_REGION": region,
        "VITE_COGNITO_USER_POOL_ID": outputs["cognito_user_pool_id"],
        "VITE_COGNITO_USER_POOL_CLIENT_ID": outputs["cognito_user_pool_client_id"],
        "VITE_COGNITO_IDENTITY_POOL_ID": outputs["cognito_identity_pool_id"],
        "VITE_RUNTIME_ARN": outputs["runtime_arn"],
        # The InvokeAgentRuntime qualifier is its OWN concern, not the endpoint
        # name: default to 'DEFAULT' and read a dedicated runtime_qualifier key
        # so the invoke contract never silently tracks an unrelated name.
        "VITE_RUNTIME_QUALIFIER": outputs.get("runtime_qualifier", "DEFAULT"),
        "VITE_RUNTIME_ENDPOINT_ARN": outputs["runtime_endpoint_arn"],
        "VITE_REGISTRY_ID": outputs["registry_id"],
        "VITE_GATEWAY_ID": outputs["gateway_id"],
        "VITE_RUNTIME_LOG_GROUP": f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT",
        "VITE_SPANS_LOG_GROUP": "aws/spans",
        "VITE_OTEL_SERVICE_NAME": outputs["otel_service_name"],
        "VITE_GUARDRAIL_ARN": guardrail_arn,
        "VITE_GUARDRAIL_VERSION": str(outputs["guardrail_version"]),
        "VITE_POLICY_ENGINE_ID": outputs["policy_engine_id"],
        "VITE_POLICY_ID": outputs["policy_id"],
        "VITE_EVAL_NAMESPACE": "Bedrock-AgentCore/Evaluations",
        "VITE_BEDROCK_MODEL_ID": outputs.get(
            "bedrock_model_id", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        ),
    }


# --- 1. app ------------------------------------------------------------------


def _find_app(amplify, name: str) -> dict | None:
    paginator = amplify.get_paginator("list_apps")
    for page in paginator.paginate():
        for app in page.get("apps", []):
            if app.get("name") == name:
                return app
    return None


def ensure_app(amplify, env_vars: dict) -> str:
    """Create-if-absent the Amplify app; return appId. Idempotent by name."""
    build_spec = AMPLIFY_YML.read_text() if AMPLIFY_YML.exists() else None
    app = _find_app(amplify, APP_NAME)
    if app:
        app_id = app["appId"]
        print(f"Reusing existing Amplify app '{APP_NAME}': {app_id}")
        kwargs = dict(
            appId=app_id,
            platform="WEB",
            environmentVariables=env_vars,
            customRules=[SPA_REWRITE_RULE],
        )
        if build_spec:
            kwargs["buildSpec"] = build_spec
        amplify.update_app(**kwargs)
        return app_id

    kwargs = dict(
        name=APP_NAME,
        description="Advisor Copilot SPA (direct mode) — Amplify Hosting + Cognito.",
        platform="WEB",
        environmentVariables=env_vars,
        customRules=[SPA_REWRITE_RULE],
        tags={PROJECT_TAG_KEY: PROJECT_TAG_VALUE},
    )
    if build_spec:
        kwargs["buildSpec"] = build_spec
    resp = amplify.create_app(**kwargs)
    app_id = resp["app"]["appId"]
    print(f"Created Amplify app '{APP_NAME}': {app_id}")
    return app_id


def ensure_branch(amplify, app_id: str, env_vars: dict) -> str:
    """Create-if-absent the branch; return its name. Idempotent."""
    try:
        amplify.get_branch(appId=app_id, branchName=BRANCH_NAME)
        print(f"Reusing existing branch '{BRANCH_NAME}'")
        amplify.update_branch(
            appId=app_id,
            branchName=BRANCH_NAME,
            environmentVariables=env_vars,
            enableAutoBuild=False,
        )
        return BRANCH_NAME
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NotFoundException":
            raise

    amplify.create_branch(
        appId=app_id,
        branchName=BRANCH_NAME,
        environmentVariables=env_vars,
        enableAutoBuild=False,
        tags={PROJECT_TAG_KEY: PROJECT_TAG_VALUE},
    )
    print(f"Created branch '{BRANCH_NAME}'")
    return BRANCH_NAME


# --- 2. build + zip + manual deploy -----------------------------------------


def build_spa(env_vars: dict, region: str) -> Path:
    """Build the SPA under ui/ with the VITE_* env injected; return the dist dir."""
    build_env = os.environ.copy()
    build_env.update(env_vars)
    print("Building SPA (npm ci && npm run build) with direct-mode VITE_* env...")
    subprocess.run(["npm", "ci"], cwd=UI_DIR, env=build_env, check=True)
    subprocess.run(["npm", "run", "build"], cwd=UI_DIR, env=build_env, check=True)
    if not UI_DIST.is_dir():
        sys.exit(f"Build did not produce {UI_DIST}")
    return UI_DIST


def _zip_dist(dist_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(dist_dir).as_posix())
    return buf.getvalue()


def _cancel_pending_jobs(amplify, app_id: str, branch_name: str) -> None:
    """Stop any unfinished job so a fresh deployment can be created (idempotency)."""
    pending = {"PENDING", "PROVISIONING", "RUNNING"}
    try:
        jobs = amplify.list_jobs(
            appId=app_id, branchName=branch_name, maxResults=50
        ).get("jobSummaries", [])
    except ClientError:
        return
    for job in jobs:
        if job.get("status") in pending:
            job_id = job["jobId"]
            print(f"  stopping unfinished job {job_id} ({job['status']})...")
            try:
                amplify.stop_job(appId=app_id, branchName=branch_name, jobId=job_id)
            except ClientError as exc:
                print(f"  (stop_job {job_id} ignored: {exc})")


def deploy_artifact(amplify, app_id: str, branch_name: str, dist_dir: Path) -> str:
    """Manual zip deploy: create_deployment -> upload -> start_deployment. Returns jobId."""
    _cancel_pending_jobs(amplify, app_id, branch_name)
    dep = amplify.create_deployment(appId=app_id, branchName=branch_name)
    job_id = dep["jobId"]
    zip_url = dep["zipUploadUrl"]

    zip_bytes = _zip_dist(dist_dir)
    print(f"Uploading SPA artifact ({len(zip_bytes)} bytes) to Amplify...")
    req = Request(
        zip_url,
        data=zip_bytes,
        method="PUT",
        headers={"Content-Type": "application/zip"},
    )
    # Verify TLS against certifi's CA bundle (system Python may lack a CA store).
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    with urlopen(req, context=ssl_ctx) as resp:  # noqa: S310 - presigned Amplify URL
        if resp.status not in (200, 204):
            sys.exit(f"Artifact upload failed: HTTP {resp.status}")

    amplify.start_deployment(appId=app_id, branchName=branch_name, jobId=job_id)
    print(f"Started deployment job {job_id}.")
    return job_id


def poll_job(amplify, app_id: str, branch_name: str, job_id: str) -> str:
    terminal_fail = {"FAILED", "CANCELLED"}
    for _ in range(POLL_MAX_ATTEMPTS):
        job = amplify.get_job(appId=app_id, branchName=branch_name, jobId=job_id)["job"]
        status = job["summary"]["status"]
        if status == "SUCCEED":
            print(f"Deployment job {job_id} SUCCEED.")
            return status
        if status in terminal_fail:
            raise RuntimeError(f"Deployment job {job_id} {status}.")
        print(f"  deploy status={status}; waiting...")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Deployment job {job_id} did not finish in time.")


# --- --verify ----------------------------------------------------------------


def verify(amplify, outputs: dict) -> None:
    app_id = outputs.get("amplify_app_id")
    if not app_id:
        sys.exit("outputs.json missing amplify_app_id; run the script first.")
    app = amplify.get_app(appId=app_id)["app"]
    branch = amplify.get_branch(appId=app_id, branchName=BRANCH_NAME)["branch"]
    domain = outputs.get("amplify_default_domain", f"{BRANCH_NAME}.{app_id}.amplifyapp.com")
    print(f"Amplify app: {app['name']} ({app_id})")
    print(f"Branch: {branch['branchName']} (stage={branch.get('stage')})")
    print(f"Default domain: https://{domain}")
    print(f"Active job id: {branch.get('activeJobId')}")
    env = branch.get("environmentVariables", {}) or app.get("environmentVariables", {})
    if env.get("VITE_DATA_MODE") != "direct":
        print("  !! VITE_DATA_MODE is not 'direct'")
        sys.exit(1)
    print("  VITE_DATA_MODE=direct (ok)")


# --- orchestration -----------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="Describe the app/branch + print the default domain.")
    args = parser.parse_args()

    region = _region()
    amplify = boto3.client("amplify", region_name=region)
    outputs = _read_outputs()

    if args.verify:
        verify(amplify, outputs)
        return

    _require_outputs(outputs)
    env_vars = _vite_env(region, outputs)

    app_id = ensure_app(amplify, env_vars)
    branch_name = ensure_branch(amplify, app_id, env_vars)

    dist_dir = build_spa(env_vars, region)
    job_id = deploy_artifact(amplify, app_id, branch_name, dist_dir)
    poll_job(amplify, app_id, branch_name, job_id)

    default_domain = f"{branch_name}.{app_id}.amplifyapp.com"
    write_outputs(
        {
            "amplify_app_id": app_id,
            "amplify_branch": branch_name,
            "amplify_default_domain": default_domain,
            "amplify_last_job_id": job_id,
        }
    )
    print(f"\nWrote amplify outputs to {OUTPUTS_PATH} (gitignored).")
    print(f"  amplify_app_id={app_id}")
    print(f"  amplify_default_domain=https://{default_domain}")


if __name__ == "__main__":
    main()
