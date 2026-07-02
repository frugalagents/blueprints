from __future__ import annotations

import json
from typing import Any

from api_kg.llm.bedrock_client import BedrockClient


def repair_plan(plan: dict[str, Any], violations: list[str], retrieval_result: dict, config: dict) -> dict[str, Any]:
    try:
        client = BedrockClient(config.get("bedrock", {}))
        return client.converse_json(_repair_prompt(plan, violations, retrieval_result))
    except Exception:
        return plan


def _repair_prompt(plan: dict, violations: list[str], retrieval_result: dict) -> str:
    available_caps = [c["operation_id"] for c in retrieval_result.get("matched_capabilities", [])]
    return f"""This execution plan failed validation. Fix it and return valid JSON.

PLAN:
{json.dumps(plan, indent=2)}

VIOLATIONS:
{json.dumps(violations, indent=2)}

AVAILABLE CAPABILITIES (use only these):
{json.dumps(available_caps)}

RULES:
- Every "capability" in api_call steps must be from the available list
- Every step must have a unique "id"
- Step types: "api_call" or "operator"
- Operators: "diff", "attribute_delta", "reconcile_total", "extract_field", "sum"
- Path parameters must be provided in args (e.g. employee_id)
- Args can reference inputs with "$inputs.field" or previous steps with "$steps.step_id"

Return ONLY the corrected plan as JSON."""
