from __future__ import annotations

import re
from typing import Any


def extract_inputs(question: str, retrieval_result: dict, config: dict, use_bedrock: bool = True) -> dict[str, Any]:
    """Extract input parameters from the question using graph-derived entity types.

    No hardcoded entity patterns. Uses:
    1. The graph's parameter nodes to know what inputs exist
    2. Regex for obvious patterns (dates, IDs with prefixes)
    3. LLM for ambiguous extraction (optional)
    4. Config aliases for demo fixtures
    """
    inputs: dict[str, Any] = {}

    # Collect all possible input parameters from retrieved capabilities
    known_params = _collect_known_params(retrieval_result)

    # 1. Extract ID-like patterns (PREFIX-NUMBER)
    for match in re.findall(r"\b([A-Z]{1,10}-\d+)\b", question):
        # Find which parameter this ID likely maps to
        param = _match_id_to_param(match, known_params)
        if param:
            inputs[param] = match

    # 2. Extract date patterns
    date_matches = re.findall(r"\b(20\d{2}-\d{2}(?:-\d{2})?)\b", question)
    if date_matches:
        period_params = [p for p in known_params if "period" in p or "date" in p or "since" in p]
        if period_params:
            inputs[period_params[0]] = date_matches[0]
            if len(date_matches) > 1 and len(period_params) > 1:
                inputs[period_params[1]] = date_matches[1]

    # 3. Apply config aliases (for demo fixtures — not hardcoded logic, user-configured)
    aliases = config.get("demo_defaults", {}).get("aliases", {})
    q_lower = question.lower()
    for alias, values in aliases.items():
        if alias.lower() in q_lower:
            inputs.update(values)

    # 4. Apply demo defaults for period params if not already set
    defaults = config.get("demo_defaults", {})
    for param in known_params:
        if "current_period" in param and param not in inputs:
            inputs[param] = defaults.get("current_period", "")
        if "previous_period" in param and param not in inputs:
            inputs[param] = defaults.get("previous_period", "")

    # 5. LLM extraction for complex cases (if enabled and we have unresolved path params)
    if use_bedrock and _has_unresolved_params(retrieval_result, inputs):
        llm_inputs = _llm_extract(question, known_params, inputs, config)
        # Only add LLM-extracted values for params we don't already have
        for k, v in llm_inputs.items():
            if k not in inputs and v:
                inputs[k] = v

    return inputs


def _collect_known_params(retrieval_result: dict) -> set[str]:
    """Get all input parameter names from retrieved capabilities."""
    params = set()
    for cap in retrieval_result.get("matched_capabilities", []):
        for field in cap.get("input_fields", []):
            name = field.get("name", "")
            if name:
                params.add(name)
    return params


def _match_id_to_param(id_value: str, known_params: set[str]) -> str:
    """Match an extracted ID to the most likely parameter name."""
    prefix = id_value.split("-")[0].lower()
    # Find params that contain the prefix or standard ID suffixes
    for param in known_params:
        param_lower = param.lower()
        if prefix in param_lower:
            return param
        if param_lower.endswith("_id") and prefix in param_lower.replace("_id", ""):
            return param
    # Default to any *_id param
    id_params = [p for p in known_params if p.endswith("_id")]
    return id_params[0] if id_params else ""


def _has_unresolved_params(retrieval_result: dict, inputs: dict) -> bool:
    """Check if there are required path params we haven't resolved."""
    for cap in retrieval_result.get("matched_capabilities", []):
        for field in cap.get("input_fields", []):
            if field.get("location") == "path" and field.get("name") not in inputs:
                return True
    return False


def _llm_extract(question: str, known_params: set[str], existing_inputs: dict, config: dict) -> dict[str, Any]:
    """Use LLM to extract entity values from the question."""
    try:
        from api_kg.llm.bedrock_client import BedrockClient
        client = BedrockClient(config.get("bedrock", {}))

        # Only ask about params we haven't resolved
        unresolved = [p for p in known_params if p not in existing_inputs]
        if not unresolved:
            return {}

        prompt = f"""Extract parameter values from this question. Return JSON object mapping parameter names to values.

Question: "{question}"

Parameters to extract (return empty string if not found):
{unresolved}

Already extracted: {existing_inputs}

Return ONLY a JSON object like {{"param_name": "value", ...}}. Use empty string for unknown values."""

        result = client.converse_json(prompt, model_key="synthesis_model")
        if isinstance(result, dict):
            return {k: v for k, v in result.items() if v and k in known_params}
    except Exception:
        pass
    return {}
