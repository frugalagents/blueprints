from __future__ import annotations

from typing import Any


def build_evidence(plan: dict, execution: dict) -> dict:
    claims = []
    trace = execution.get("trace", {})

    # Extract claims from operators (diff/attribution)
    for step_id, item in trace.items():
        if item.get("status") != "success":
            continue
        step = item.get("step", {})
        output = item.get("output", {})
        data = output.get("data") if isinstance(output, dict) else output
        if step.get("type") == "operator" and step.get("operator") == "diff":
            claims.extend(_claims_from_diff(step_id, data, trace))
        if step.get("type") == "operator" and step.get("operator") == "attribute_delta":
            claims.extend(_claims_from_attribution(step_id, data))

    # If no operator claims, extract evidence from API call responses directly
    if not claims:
        for step_id, item in trace.items():
            if item.get("status") != "success":
                continue
            step = item.get("step", {})
            if step.get("type") == "api_call":
                output = item.get("output", {})
                data = output.get("data") if isinstance(output, dict) and "data" in output else output
                claims.extend(_claims_from_api_response(step_id, step.get("capability", ""), data))

    claims = _dedupe_claims(claims)
    return {
        "question": plan.get("question"),
        "plan_id": plan.get("plan_id"),
        "success": execution.get("success"),
        "claims": claims,
        "step_count": len(trace),
    }


def _claims_from_api_response(step_id: str, capability: str, data: Any) -> list[dict]:
    if not isinstance(data, dict):
        return []
    claims = []
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) and value != "" and key not in ("operation_id",):
            claims.append({
                "claim": f"{key}: {value}",
                "field": key,
                "value": value,
                "source": capability,
                "step": step_id,
                "confidence": 1.0,
            })
        elif isinstance(value, list) and value:
            # Summarize lists (e.g., list of checks, medications, etc.)
            if all(isinstance(item, dict) for item in value):
                summary_fields = _summarize_list(key, value)
                claims.extend(summary_fields)
            else:
                claims.append({
                    "claim": f"{key}: {len(value)} items",
                    "field": key,
                    "value": f"{len(value)} items",
                    "source": capability,
                    "step": step_id,
                    "confidence": 1.0,
                })
    return claims


def _summarize_list(field_name: str, items: list[dict]) -> list[dict]:
    claims = []
    for item in items[:10]:
        # Build a readable summary from the dict
        parts = []
        for k, v in item.items():
            if isinstance(v, (str, int, float, bool)) and v != "":
                parts.append(f"{k}={v}")
        if parts:
            summary = ", ".join(parts[:5])
            claims.append({
                "claim": f"{field_name} item: {summary}",
                "field": field_name,
                "value": summary,
                "source": "api_response",
                "confidence": 1.0,
            })
    return claims


def _dedupe_claims(claims: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for claim in claims:
        key = claim.get("claim", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
    return out


def _claims_from_diff(step_id: str, data: Any, trace: dict) -> list[dict]:
    if not isinstance(data, dict):
        return []
    claims = []
    for field, change in data.get("fields", {}).items():
        if isinstance(change, dict) and "delta" in change:
            claims.append(
                {
                    "claim": f"{field} changed by {change['delta']}",
                    "field": field,
                    "previous": change.get("previous"),
                    "current": change.get("current"),
                    "delta": change.get("delta"),
                    "sources": _api_sources(trace),
                    "confidence": 1.0,
                }
            )
        elif isinstance(change, dict):
            for nested, nested_change in change.items():
                if isinstance(nested_change, dict) and "delta" in nested_change:
                    claims.append(
                        {
                            "claim": f"{field}.{nested} changed by {nested_change['delta']}",
                            "field": f"{field}.{nested}",
                            "previous": nested_change.get("previous"),
                            "current": nested_change.get("current"),
                            "delta": nested_change.get("delta"),
                            "sources": _api_sources(trace),
                            "confidence": 1.0,
                        }
                    )
    return claims


def _claims_from_attribution(step_id: str, data: Any) -> list[dict]:
    if not isinstance(data, dict):
        return []
    claims = []
    for item in data.get("contributors", [])[:8]:
        claims.append(
            {
                "claim": f"{item.get('field')} contributed {item.get('delta')}",
                "field": item.get("field"),
                "previous": item.get("previous"),
                "current": item.get("current"),
                "delta": item.get("delta"),
                "sources": [{"step": step_id, "type": "operator"}],
                "confidence": 0.9,
            }
        )
    return claims


def _api_sources(trace: dict) -> list[dict]:
    sources = []
    for step_id, item in trace.items():
        step = item.get("step", {})
        if step.get("type") == "api_call":
            request = item.get("output", {}).get("request", {})
            sources.append({"step": step_id, "capability": step.get("capability"), "request": request})
    return sources
