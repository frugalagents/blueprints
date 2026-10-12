"""Idempotent provisioner for the Advisor Copilot Bedrock Guardrail.

Creates (or reuses) a single Bedrock Guardrail named 'advisor-copilot-guardrail'
in the configured region, publishes an immutable version, and records the
id + version into infra/outputs.json so the Phase 2 agent can attach it at
invocation time.

Idempotency: the script lists existing guardrails and reuses the one matching
GUARDRAIL_NAME if present, rather than creating a duplicate. The guardrail id is
therefore stable across runs.

Climbing versions / drift warning: Bedrock guardrail versions are immutable
snapshots of the DRAFT, so each run publishes a NEW numeric version (1, then 2,
3, ...). This script always writes the latest published version into
infra/outputs.json, but the agent reads whatever GUARDRAIL_VERSION is in .env and
pins no specific version. After re-running this script you MUST sync .env's
GUARDRAIL_VERSION to the value printed below (and recorded in outputs.json) or the
agent will keep attaching the older, now-stale version. The script prints an
explicit reminder on every run.

Usage:

    AWS_REGION=us-east-1 python3 infra/01_guardrail.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import boto3

GUARDRAIL_NAME = "advisor-copilot-guardrail"
DEFAULT_REGION = "us-east-1"

# Compliance-flavored refusal messaging shown when the guardrail intervenes.
BLOCKED_INPUT_MESSAGING = (
    "This request can't be processed. The Advisor Copilot cannot discuss "
    "guaranteed returns, non-public information, or provide definitive tax or "
    "legal advice. Please rephrase within compliance guidelines."
)
BLOCKED_OUTPUT_MESSAGING = (
    "This response was withheld for compliance reasons. The Advisor Copilot "
    "cannot promise guaranteed returns, act on non-public information, or give "
    "definitive tax or legal advice. Please consult the appropriate licensed "
    "professional."
)

OUTPUTS_PATH = Path(__file__).resolve().parent / "outputs.json"


def _region() -> str:
    return os.environ.get("AWS_REGION") or DEFAULT_REGION


def _topic_policy_config() -> dict:
    return {
        "topicsConfig": [
            {
                "name": "GuaranteedReturns",
                "type": "DENY",
                "definition": (
                    "Statements promising guaranteed, riskless, or can't-lose "
                    "investment returns, or language assuring a specific profit."
                ),
                "examples": [
                    "I promise my client guaranteed 20% returns this year.",
                    "This investment is riskless and can't lose money.",
                ],
                "inputAction": "BLOCK",
                "outputAction": "BLOCK",
            },
            {
                "name": "InsiderInformation",
                "type": "DENY",
                "definition": (
                    "Requests to use or share insider information, material "
                    "non-public information (MNPI), or non-public corporate data "
                    "to make trading decisions."
                ),
                "examples": [
                    "Use this material non-public information to decide the trade.",
                    "I have insider info about the earnings, should we buy?",
                ],
                "inputAction": "BLOCK",
                "outputAction": "BLOCK",
            },
            {
                "name": "UnlicensedTaxLegalAdvice",
                "type": "DENY",
                "definition": (
                    "Providing definitive, personalized tax or legal advice as if "
                    "from a licensed professional rather than suggesting the client "
                    "consult one."
                ),
                "examples": [
                    "Tell me exactly how to structure this to avoid all taxes.",
                    "Give me the legally binding way to title this account.",
                ],
                "inputAction": "BLOCK",
                "outputAction": "BLOCK",
            },
        ]
    }


def _sensitive_information_policy_config() -> dict:
    pii = [
        "EMAIL",
        "PHONE",
        "US_SOCIAL_SECURITY_NUMBER",
        "CREDIT_DEBIT_CARD_NUMBER",
        "US_BANK_ACCOUNT_NUMBER",
    ]
    return {
        "piiEntitiesConfig": [
            {"type": entity, "action": "ANONYMIZE"} for entity in pii
        ]
    }


def _word_policy_config() -> dict:
    return {"managedWordListsConfig": [{"type": "PROFANITY"}]}


def _contextual_grounding_policy_config() -> dict:
    return {
        "filtersConfig": [
            {
                "type": "GROUNDING",
                "threshold": 0.6,
                "action": "BLOCK",
                "enabled": True,
            },
            {
                "type": "RELEVANCE",
                "threshold": 0.5,
                "action": "BLOCK",
                "enabled": True,
            },
        ]
    }


def find_existing_guardrail(client) -> str | None:
    """Return the id of an existing guardrail named GUARDRAIL_NAME, else None."""
    paginator = client.get_paginator("list_guardrails")
    for page in paginator.paginate():
        for g in page.get("guardrails", []):
            if g.get("name") == GUARDRAIL_NAME:
                return g.get("id")
    return None


def create_guardrail(client) -> str:
    """Create the guardrail with all four policy types and return its id."""
    resp = client.create_guardrail(
        name=GUARDRAIL_NAME,
        description=(
            "Advisor Copilot compliance guardrail: blocks guaranteed-return, "
            "insider-information, and unlicensed tax/legal-advice topics; "
            "anonymizes PII; filters profanity; enforces contextual grounding."
        ),
        blockedInputMessaging=BLOCKED_INPUT_MESSAGING,
        blockedOutputsMessaging=BLOCKED_OUTPUT_MESSAGING,
        topicPolicyConfig=_topic_policy_config(),
        sensitiveInformationPolicyConfig=_sensitive_information_policy_config(),
        wordPolicyConfig=_word_policy_config(),
        contextualGroundingPolicyConfig=_contextual_grounding_policy_config(),
    )
    return resp["guardrailId"]


def publish_version(client, guardrail_id: str) -> str:
    """Publish an immutable version of the guardrail and return the version."""
    resp = client.create_guardrail_version(
        guardrailIdentifier=guardrail_id,
        description="Published by infra/01_guardrail.py",
    )
    return resp["version"]


def write_outputs(guardrail_id: str, version: str, region: str) -> None:
    """Merge the guardrail id/version/region into infra/outputs.json."""
    data: dict = {}
    if OUTPUTS_PATH.exists():
        try:
            data = json.loads(OUTPUTS_PATH.read_text())
        except (ValueError, OSError):
            data = {}
    data.update(
        {
            "guardrail_id": guardrail_id,
            "guardrail_version": version,
            "region": region,
        }
    )
    OUTPUTS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def main() -> None:
    region = _region()
    client = boto3.client("bedrock", region_name=region)

    existing = find_existing_guardrail(client)
    if existing:
        guardrail_id = existing
        print(f"Reusing existing guardrail '{GUARDRAIL_NAME}': {guardrail_id}")
    else:
        guardrail_id = create_guardrail(client)
        print(f"Created guardrail '{GUARDRAIL_NAME}': {guardrail_id}")

    version = publish_version(client, guardrail_id)
    print(f"Published guardrail version: {version}")

    write_outputs(guardrail_id, version, region)
    print(f"Wrote {OUTPUTS_PATH}")
    print(f"guardrail_id={guardrail_id} guardrail_version={version} region={region}")
    print(
        "REMINDER: guardrail versions are immutable and climb on each run. "
        f"Set GUARDRAIL_VERSION={version} in your gitignored .env so the agent "
        "attaches this freshly published version (outputs.json already updated)."
    )


if __name__ == "__main__":
    main()
