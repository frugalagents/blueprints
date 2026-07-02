from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from api_kg.models import Capability


def generate_descriptions(
    capabilities: list[Capability],
    graph_file: str | Path,
    config: dict[str, Any],
    output_dir: str | Path = "semantic",
    use_bedrock: bool = True,
) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if use_bedrock:
        try:
            _generate_with_llm(capabilities, graph_file, config, out)
        except Exception as e:
            print(f"LLM generation failed ({e}), falling back to spec-derived descriptions")
            _generate_from_specs(capabilities, out)
    else:
        _generate_from_specs(capabilities, out)

    print(f"Generated {len(capabilities)} descriptions -> {out}/")
    return out


def _generate_with_llm(
    capabilities: list[Capability],
    graph_file: str | Path,
    config: dict[str, Any],
    out: Path,
) -> None:
    from api_kg.llm.bedrock_client import BedrockClient
    from api_kg.graph.graph_store import load_graph

    client = BedrockClient(config.get("bedrock", {}))
    graph = load_graph(graph_file)

    # Batch capabilities for LLM enrichment (groups of 5)
    batch_size = 5
    for i in range(0, len(capabilities), batch_size):
        batch = capabilities[i:i + batch_size]
        batch_summaries = []
        for cap in batch:
            # Gather graph context: dependencies, related entities
            deps = []
            related = []
            if graph.has_node(cap.operation_id):
                for succ in graph.successors(cap.operation_id):
                    edge = graph.get_edge_data(cap.operation_id, succ, {})
                    if edge.get("edge_type") == "capability_depends_on":
                        related.append(succ)
                for pred in graph.predecessors(cap.operation_id):
                    edge = graph.get_edge_data(pred, cap.operation_id, {})
                    if edge.get("edge_type") == "capability_depends_on":
                        deps.append(pred)

            batch_summaries.append({
                "operation_id": cap.operation_id,
                "domain": cap.domain,
                "method": cap.method,
                "path": cap.path,
                "summary": cap.summary,
                "description": cap.description,
                "inputs": [f.name for f in cap.input_fields],
                "outputs": [f.name for f in cap.output_fields],
                "produces_entities": cap.produces_entities,
                "consumes_entities": cap.consumes_entities,
                "depends_on": deps,
                "depended_by": related,
                "side_effect": cap.side_effect,
            })

        prompt = _enrichment_prompt(batch_summaries)
        try:
            result = client.converse_json(prompt, model_key="inference_model")
            if isinstance(result, list):
                for desc in result:
                    _write_description(out, desc)
            elif isinstance(result, dict) and "capabilities" in result:
                for desc in result["capabilities"]:
                    _write_description(out, desc)
        except Exception:
            # Fall back to spec-derived for this batch
            for cap in batch:
                _write_spec_description(out, cap)


def _generate_from_specs(capabilities: list[Capability], out: Path) -> None:
    for cap in capabilities:
        _write_spec_description(out, cap)


def _write_spec_description(out: Path, cap: Capability) -> None:
    desc = {
        "capability": cap.operation_id,
        "domain": cap.domain,
        "method": cap.method,
        "path": cap.path,
        "summary": cap.summary or f"{cap.method} {cap.path}",
        "description": cap.description or cap.summary or f"Calls {cap.method} {cap.path}",
        "side_effect": cap.side_effect,
        "preconditions": [f"Requires {f.name}" for f in cap.input_fields if f.required and f.location == "path"],
        "produces": [f.name for f in cap.output_fields[:10]],
        "consumes": [f.name for f in cap.input_fields if f.required],
        "use_cases": [],
        "related": [],
    }
    _write_description(out, desc)


def _write_description(out: Path, desc: dict) -> None:
    cap_id = desc.get("capability", desc.get("operation_id", "unknown"))
    domain = desc.get("domain", "unknown")
    domain_dir = out / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    with open(domain_dir / f"{cap_id}.yaml", "w") as f:
        yaml.dump(desc, f, default_flow_style=False, sort_keys=False)


def _enrichment_prompt(batch: list[dict]) -> str:
    return json.dumps({
        "instructions": """For each API capability below, generate a rich semantic description. Return a JSON array with one object per capability.

Each object MUST have these fields:
- capability: the operation_id (keep as-is)
- domain: the domain (keep as-is)
- method: HTTP method (keep as-is)
- path: API path (keep as-is)
- summary: one-line summary (improve from original if needed)
- description: 2-4 sentence natural language description of what this API does, when you'd use it, and what business questions it helps answer
- side_effect: read/write/compute (keep as-is)
- preconditions: list of conditions that must be true before calling this API
- produces: list of data/insights this API produces (natural language, not field names)
- use_cases: 2-3 example business questions this API helps answer
- related: list of related operation_ids and WHY they're related (one sentence each)

Be specific and business-oriented. Think about what questions a person would ask that would need this API.""",
        "capabilities": batch,
    })


def load_descriptions(semantic_dir: str | Path) -> list[dict]:
    root = Path(semantic_dir)
    if not root.exists():
        return []
    descriptions = []
    for yaml_file in sorted(root.rglob("*.yaml")):
        if yaml_file.name == "communities.yaml":
            continue
        with open(yaml_file) as f:
            desc = yaml.safe_load(f)
        if desc and isinstance(desc, dict) and "capability" in desc:
            descriptions.append(desc)
    return descriptions
