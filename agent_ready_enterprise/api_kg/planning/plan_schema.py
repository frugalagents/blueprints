from __future__ import annotations

from typing import Any


SUPPORTED_STEP_TYPES = {"api_call", "operator"}
SUPPORTED_OPERATORS = {"diff", "attribute_delta", "reconcile_total", "extract_field", "sum"}


def empty_plan(question: str) -> dict[str, Any]:
    return {
        "plan_id": "plan_001",
        "question": question,
        "goal": "answer_question",
        "inputs": {},
        "steps": [],
    }
