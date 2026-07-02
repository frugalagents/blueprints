from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api_kg.graph.graph_store import load_graph
from api_kg.planning.plan_schema import SUPPORTED_OPERATORS, SUPPORTED_STEP_TYPES


@dataclass
class PlanValidationResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_plan(plan: dict[str, Any], graph_file: str | Path, config: dict) -> PlanValidationResult:
    graph = load_graph(graph_file)
    violations: list[str] = []
    warnings: list[str] = []
    max_steps = config.get("planning", {}).get("max_steps", 12)
    allow_mutations = config.get("planning", {}).get("allow_mutations", False)

    steps = plan.get("steps", [])
    if len(steps) > max_steps:
        violations.append(f"Plan has {len(steps)} steps; max is {max_steps}")

    seen_steps: set[str] = set()
    for step in steps:
        step_id = step.get("id")
        step_type = step.get("type")
        if not step_id:
            violations.append("Step missing id")
        elif step_id in seen_steps:
            violations.append(f"Duplicate step id: {step_id}")
        else:
            seen_steps.add(step_id)
        if step_type not in SUPPORTED_STEP_TYPES:
            violations.append(f"Unsupported step type for {step_id}: {step_type}")
            continue
        if step_type == "api_call":
            cap_id = step.get("capability")
            if not graph.has_node(cap_id) or graph.nodes[cap_id].get("node_type") != "capability":
                violations.append(f"Unknown capability in {step_id}: {cap_id}")
                continue
            cap = graph.nodes[cap_id]
            if not allow_mutations and cap.get("side_effect") == "write":
                violations.append(f"Mutation capability not allowed in {step_id}: {cap_id}")
            missing = [p for p in cap.get("path_params", []) if p not in step.get("args", {})]
            if missing:
                violations.append(f"Missing path args for {step_id}/{cap_id}: {', '.join(missing)}")
        if step_type == "operator" and step.get("operator") not in SUPPORTED_OPERATORS:
            violations.append(f"Unsupported operator in {step_id}: {step.get('operator')}")

    return PlanValidationResult(passed=not violations, violations=violations, warnings=warnings)
