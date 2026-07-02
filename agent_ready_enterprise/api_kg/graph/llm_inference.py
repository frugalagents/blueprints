from __future__ import annotations

import json
from typing import Any

from api_kg.llm.bedrock_client import BedrockClient
from api_kg.models import Capability, DependencyEdge


BATCH_SIZE = 10

PROMPT_TEMPLATE = """You are an API dependency analyst. Given these API capabilities, identify data flow dependencies between them.

A dependency means: operation B needs data that operation A produces, OR operation B logically should be called after operation A.

Capabilities:
{capabilities_json}

Return a JSON array of dependencies. Each item:
{{"source": "operation_id_that_produces_data", "target": "operation_id_that_consumes_data", "reason": "brief explanation", "confidence": 0.0-1.0}}

Rules:
- Only include dependencies with confidence >= 0.4
- source and target must be operation_ids from the list above
- Do not include self-dependencies
- Focus on data flow and logical ordering, not just shared parameters

Return ONLY the JSON array, no other text."""


def infer_dependencies_llm(capabilities: list[Capability], config: dict[str, Any]) -> list[DependencyEdge]:
    try:
        client = BedrockClient(config.get("bedrock", {}))
    except Exception:
        return []

    edges: list[DependencyEdge] = []
    batches = _batch_capabilities(capabilities, BATCH_SIZE)

    for batch in batches:
        batch_edges = _infer_batch(client, batch)
        edges.extend(batch_edges)

    return edges


def _batch_capabilities(capabilities: list[Capability], size: int) -> list[list[Capability]]:
    batches = []
    for i in range(0, len(capabilities), size):
        batches.append(capabilities[i:i + size])
    # If multiple batches, also create cross-batch pairs for cross-domain inference
    if len(batches) > 1:
        domains = {}
        for cap in capabilities:
            domains.setdefault(cap.domain, []).append(cap)
        domain_list = list(domains.values())
        for i in range(len(domain_list)):
            for j in range(i + 1, len(domain_list)):
                cross = domain_list[i][:5] + domain_list[j][:5]
                if len(cross) > 1:
                    batches.append(cross)
    return batches


def _infer_batch(client: BedrockClient, capabilities: list[Capability]) -> list[DependencyEdge]:
    cap_summaries = [
        {
            "operation_id": c.operation_id,
            "domain": c.domain,
            "method": c.method,
            "path": c.path,
            "summary": c.summary,
            "inputs": [f.name for f in c.input_fields],
            "outputs": [f.name for f in c.output_fields],
            "produces_entities": c.produces_entities,
            "consumes_entities": c.consumes_entities,
        }
        for c in capabilities
    ]
    valid_ids = {c.operation_id for c in capabilities}
    prompt = PROMPT_TEMPLATE.format(capabilities_json=json.dumps(cap_summaries, indent=2))

    try:
        result = client.converse_json(prompt, model_key="inference_model")
    except Exception:
        return []

    if not isinstance(result, list):
        return []

    edges = []
    for item in result:
        source = item.get("source", "")
        target = item.get("target", "")
        if source not in valid_ids or target not in valid_ids:
            continue
        if source == target:
            continue
        confidence = min(1.0, max(0.0, float(item.get("confidence", 0.5))))
        if confidence < 0.4:
            continue
        edges.append(
            DependencyEdge(
                source=source,
                target=target,
                confidence=confidence,
                dependency_type="data_flow",
                methods=["llm_semantic"],
                reasons=[item.get("reason", "LLM-inferred dependency")],
            )
        )
    return edges
