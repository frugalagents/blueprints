from __future__ import annotations

import hashlib
import json
from typing import Any

from api_kg.llm.bedrock_client import BedrockClient
from api_kg.planning.plan_schema import SUPPORTED_OPERATORS, SUPPORTED_STEP_TYPES, empty_plan
from api_kg.planning.input_extractor import extract_inputs


_PLAN_CACHE: dict[str, dict] = {}


def generate_plan(question: str, retrieval_result: dict, config: dict, use_bedrock: bool = True) -> dict:
    cache_key = _cache_key(question, retrieval_result)
    if cache_key in _PLAN_CACHE:
        return _PLAN_CACHE[cache_key]

    inputs = extract_inputs(question, retrieval_result, config, use_bedrock=use_bedrock)
    capabilities = retrieval_result.get("matched_capabilities", [])

    if not capabilities:
        return empty_plan(question)

    # Try pre-computed plan template cache first (sub-second)
    if use_bedrock:
        from api_kg.planning.plan_cache import match_cached_plan

        cached = match_cached_plan(question, config)
        if cached:
            cached["inputs"] = inputs
            _PLAN_CACHE[cache_key] = cached
            return cached

    # Cache miss — generate with LLM
    if use_bedrock:
        try:
            plan = _llm_plan(question, inputs, capabilities, retrieval_result, config)
            _PLAN_CACHE[cache_key] = plan
            return plan
        except Exception:
            pass

    # Fallback: call top capabilities that have resolvable params
    plan = _fallback_plan(question, inputs, capabilities)
    _PLAN_CACHE[cache_key] = plan
    return plan


def _llm_plan(question: str, inputs: dict, capabilities: list[dict], retrieval_result: dict, config: dict) -> dict:
    client = BedrockClient(config.get("bedrock", {}))

    # Build capability context for the LLM (from semantic descriptions if available)
    cap_context = []
    for cap in capabilities[:10]:
        cap_context.append({
            "operation_id": cap.get("operation_id"),
            "domain": cap.get("domain"),
            "method": cap.get("method"),
            "path": cap.get("path"),
            "summary": cap.get("summary"),
            "inputs": [f.get("name") for f in cap.get("input_fields", []) if f.get("required") or f.get("location") == "path"],
            "outputs": [f.get("name") for f in cap.get("output_fields", [])[:8]],
            "side_effect": cap.get("side_effect"),
        })

    # Dependency constraints from graph
    dep_constraints = []
    for edge in retrieval_result.get("edges", []):
        dep_constraints.append(f"{edge.get('from')} must execute before {edge.get('to')}")

    prompt = json.dumps({
        "task": "Generate an execution plan to answer this question using the available API capabilities.",
        "question": question,
        "extracted_inputs": inputs,
        "available_capabilities": cap_context,
        "dependency_constraints": dep_constraints,
        "plan_schema": {
            "plan_id": "plan_001",
            "question": "the original question",
            "goal": "brief goal description",
            "inputs": "extracted inputs dict",
            "steps": [
                {
                    "id": "unique_step_id",
                    "type": "api_call OR operator",
                    "capability": "operation_id (for api_call)",
                    "operator": f"one of {list(SUPPORTED_OPERATORS)} (for operator type)",
                    "args": {"param_name": "$inputs.field OR $steps.prev_step_id OR literal_value"},
                },
            ],
        },
        "rules": [
            "Use $inputs.field_name to reference extracted inputs",
            "Use $steps.step_id to reference output of a previous step",
            "Only use capabilities from the available list",
            "Respect dependency constraints in step ordering",
            "Use 'diff' operator to compare two API responses",
            "Use 'attribute_delta' operator to explain what changed",
            "Keep plans concise — minimum steps needed to answer the question",
            "Path parameters (employee_id, state, etc.) must be provided in args",
        ],
    }, indent=2)

    # Use synthesis_model (Haiku) for faster planning — accuracy is maintained
    # because the plan is validated against graph constraints afterward
    result = client.converse_json(prompt, model_key="synthesis_model")

    # Ensure required fields
    if not isinstance(result, dict):
        raise ValueError("LLM returned non-dict plan")
    result.setdefault("plan_id", "plan_001")
    result.setdefault("question", question)
    result.setdefault("goal", "answer_question")
    result.setdefault("inputs", inputs)
    result.setdefault("steps", [])

    return result


def _fallback_plan(question: str, inputs: dict, capabilities: list[dict]) -> dict:
    """Fallback when no LLM available: call top resolvable capabilities."""
    plan = empty_plan(question)
    plan["inputs"] = inputs

    added = 0
    for cap in capabilities:
        if added >= 5:
            break
        args = _resolve_args(cap, inputs)
        # Skip if required path params can't be resolved
        path_params = [f.get("name") for f in cap.get("input_fields", []) if f.get("location") == "path"]
        if any(p not in args for p in path_params if p):
            continue
        plan["steps"].append({
            "id": f"call_{added + 1}",
            "type": "api_call",
            "capability": cap["operation_id"],
            "args": args,
        })
        added += 1

    return plan


def _resolve_args(cap: dict, inputs: dict) -> dict:
    """Resolve capability args from available inputs."""
    args: dict[str, Any] = {}
    for field in cap.get("input_fields", []):
        name = field.get("name")
        if not name:
            continue
        if name in inputs:
            args[name] = f"$inputs.{name}"
        elif field.get("location") == "path":
            # Try partial match for path params
            for input_key in inputs:
                if name in input_key or input_key in name:
                    args[name] = f"$inputs.{input_key}"
                    break
    return args


def _cache_key(question: str, retrieval_result: dict) -> str:
    caps = sorted(c.get("operation_id", "") for c in retrieval_result.get("matched_capabilities", []))
    raw = f"{question.lower().strip()}|{'|'.join(caps)}"
    return hashlib.md5(raw.encode()).hexdigest()
