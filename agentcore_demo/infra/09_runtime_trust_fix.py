"""Phase 5 FOLLOW-UP: tighten the Advisor Copilot runtime role trust policy.

The Phase-5 runtime execution role `advisor-copilot-runtime-role` was created
with a confused-deputy `aws:SourceArn` condition scoped too broadly:

    arn:aws:bedrock-agentcore:<region>:<acct>:*   (a bare ":*")

That wildcard matches EVERY bedrock-agentcore resource type in the account
(gateways, memories, workload identities, ...), not just runtimes. This script
tightens the `ArnLike aws:SourceArn` value to the runtime resource scope:

    arn:aws:bedrock-agentcore:<region>:<acct>:runtime/*

The `aws:SourceAccount` StringEquals condition and the rest of the trust
document are left untouched. Only the single over-broad `ArnLike` value is
rewritten in place.

Idempotency: the current AssumeRolePolicy is read via iam.get_role; if the
SourceArn already ends in `:runtime/*` the script prints "already tightened"
and makes no API write. Otherwise it rewrites just the matching value and calls
iam.update_assume_role_policy, then re-reads to confirm.

The account id is resolved at runtime from sts.get_caller_identity and is NEVER
hardcoded in this tracked file (redaction gate).

Usage:
    AWS_REGION=us-east-1 .venv/bin/python infra/09_runtime_trust_fix.py
    AWS_REGION=us-east-1 .venv/bin/python infra/09_runtime_trust_fix.py --verify
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import boto3

DEFAULT_REGION = "us-east-1"
RUNTIME_ROLE_NAME = "advisor-copilot-runtime-role"
SERVICE = "bedrock-agentcore"


def _region() -> str:
    return os.environ.get("AWS_REGION") or DEFAULT_REGION


def _account_id(session: boto3.session.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def _broad_source_arn(region: str, account_id: str) -> str:
    return f"arn:aws:{SERVICE}:{region}:{account_id}:*"


def _tight_source_arn(region: str, account_id: str) -> str:
    return f"arn:aws:{SERVICE}:{region}:{account_id}:runtime/*"


def _get_trust_doc(iam) -> dict:
    resp = iam.get_role(RoleName=RUNTIME_ROLE_NAME)
    return resp["Role"]["AssumeRolePolicyDocument"]


def _current_source_arn(trust_doc: dict):
    """Return the ArnLike aws:SourceArn value from the trust doc, or None."""
    for stmt in trust_doc.get("Statement", []):
        cond = stmt.get("Condition", {})
        arnlike = cond.get("ArnLike", {})
        val = arnlike.get("aws:SourceArn")
        if val is not None:
            return val
    return None


def _tighten(trust_doc: dict, tight: str) -> bool:
    """Rewrite any ArnLike aws:SourceArn ending in a bare ':*' to the runtime
    scope. Returns True if a change was made."""
    changed = False
    for stmt in trust_doc.get("Statement", []):
        cond = stmt.get("Condition", {})
        arnlike = cond.get("ArnLike", {})
        val = arnlike.get("aws:SourceArn")
        if isinstance(val, str) and val.endswith(":*") and not val.endswith(":runtime/*"):
            arnlike["aws:SourceArn"] = tight
            changed = True
    return changed


def verify(region: str) -> int:
    session = boto3.session.Session(region_name=region)
    iam = session.client("iam")
    trust_doc = _get_trust_doc(iam)
    source_arn = _current_source_arn(trust_doc)
    print(f"role={RUNTIME_ROLE_NAME}")
    print(f"aws:SourceArn={source_arn}")
    if not isinstance(source_arn, str):
        print("FAIL: no ArnLike aws:SourceArn condition found.")
        return 1
    if source_arn.endswith(":runtime/*") and not source_arn.endswith(":*:*"):
        # ":runtime/*" ends in "*" but NOT in a bare ":*" segment.
        bare = f"arn:aws:{SERVICE}:{region}:{_account_id(session)}:*"
        if source_arn == bare:
            print("FAIL: SourceArn is still the bare ':*' scope.")
            return 1
        print("OK: SourceArn is scoped to :runtime/* (no bare ':*').")
        return 0
    print("FAIL: SourceArn does not end in ':runtime/*'.")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="Assert SourceArn ends in :runtime/* (no bare :*).")
    args = parser.parse_args()

    region = _region()

    if args.verify:
        sys.exit(verify(region))

    session = boto3.session.Session(region_name=region)
    account_id = _account_id(session)
    iam = session.client("iam")

    tight = _tight_source_arn(region, account_id)
    trust_doc = _get_trust_doc(iam)
    current = _current_source_arn(trust_doc)
    print(f"Current aws:SourceArn={current}")

    if current == tight:
        print("already tightened (SourceArn already scoped to :runtime/*); no-op.")
        return

    if not _tighten(trust_doc, tight):
        # Nothing matched the broad pattern but it is not already the tight value.
        print(
            "No bare ':*' SourceArn found to tighten and it is not already "
            f":runtime/*. Leaving trust policy unchanged (current={current})."
        )
        return

    iam.update_assume_role_policy(
        RoleName=RUNTIME_ROLE_NAME,
        PolicyDocument=json.dumps(trust_doc),
    )
    # Re-read to confirm the write landed.
    new_source_arn = _current_source_arn(_get_trust_doc(iam))
    print(f"Tightened aws:SourceArn -> {new_source_arn}")
    if new_source_arn != tight:
        sys.exit(
            f"FAIL: post-update SourceArn is {new_source_arn}, expected {tight}."
        )
    print("OK: runtime role trust tightened to :runtime/*.")


if __name__ == "__main__":
    main()
