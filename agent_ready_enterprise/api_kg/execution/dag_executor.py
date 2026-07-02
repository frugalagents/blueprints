from __future__ import annotations

from pathlib import Path
from typing import Any

from api_kg.execution.api_client import APIClient
from api_kg.execution.operators import run_operator
from api_kg.graph.graph_store import load_graph


def execute_plan(plan: dict[str, Any], graph_file: str | Path, api_base_url: str) -> dict:
    graph = load_graph(graph_file)
    client = APIClient(api_base_url)
    context: dict[str, Any] = {"inputs": plan.get("inputs", {}), "steps": {}}
    trace: dict[str, Any] = {}

    for step in plan.get("steps", []):
        step_id = step["id"]
        try:
            if step["type"] == "api_call":
                cap = dict(graph.nodes[step["capability"]])
                args = _resolve(step.get("args", {}), context)
                output = client.call(cap, args)
            elif step["type"] == "operator":
                args = _resolve(step.get("args", {}), context)
                output = {"data": run_operator(step["operator"], args)}
            else:
                raise ValueError(f"Unsupported step type: {step['type']}")
            context["steps"][step_id] = output
            trace[step_id] = {"status": "success", "step": step, "output": output}
        except Exception as exc:
            trace[step_id] = {"status": "failed", "step": step, "error": f"{type(exc).__name__}: {exc}"}
            return {"success": False, "trace": trace, "result": context}

    return {"success": True, "trace": trace, "result": context}


def _resolve(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _resolve_ref(value, context)
    if isinstance(value, dict):
        return {k: _resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, context) for v in value]
    return value


def _resolve_ref(ref: str, context: dict[str, Any]) -> Any:
    parts = ref.lstrip("$").strip(".").split(".")
    current: Any = context
    for i, part in enumerate(parts):
        if isinstance(current, dict):
            current = current.get(part)
            # Auto-unwrap API response envelope {"request":..., "data":...}
            # unless the next part explicitly asks for "request" or "data"
            if (
                isinstance(current, dict)
                and "data" in current
                and "request" in current
                and i < len(parts) - 1
                and parts[i + 1] not in ("request", "data")
            ):
                current = current["data"]
        else:
            return None
    # Final unwrap if the resolved value is still an envelope
    if isinstance(current, dict) and "data" in current and "request" in current:
        current = current["data"]
    return current
