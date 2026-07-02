from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


_PLAN_TEMPLATES_CACHE: dict[str, tuple[Any, list[dict]]] | None = None


def generate_plan_templates(semantic_dir: str | Path, graph_file: str | Path, config: dict[str, Any]) -> None:
    """Pre-compute plan templates at index time using LLM.

    For each community cluster, generate 3-5 common question patterns and their plans.
    These are cached and matched at query time via vector similarity.
    """
    import yaml
    from api_kg.llm.bedrock_client import BedrockClient
    from api_kg.semantic.descriptions import load_descriptions
    from api_kg.graph.graph_store import load_graph

    communities_path = Path(semantic_dir) / "communities.yaml"
    if not communities_path.exists():
        print("No communities.yaml found — skipping plan template generation")
        return

    with open(communities_path) as f:
        communities_data = yaml.safe_load(f)

    descriptions = load_descriptions(semantic_dir)
    desc_map = {d["capability"]: d for d in descriptions}
    graph = load_graph(graph_file)
    client = BedrockClient(config.get("bedrock", {}))

    templates = []
    for community in communities_data.get("communities", []):
        caps = community.get("capabilities", [])
        cap_descriptions = [desc_map[c] for c in caps if c in desc_map]

        if not cap_descriptions:
            continue

        prompt = json.dumps({
            "task": "Generate 4 common business questions that these API capabilities can answer, along with execution plans for each.",
            "capabilities": [
                {
                    "operation_id": d["capability"],
                    "domain": d.get("domain"),
                    "summary": d.get("summary"),
                    "inputs": [p for p in d.get("consumes", []) if isinstance(p, str)],
                }
                for d in cap_descriptions[:8]
            ],
            "output_format": [
                {
                    "question_pattern": "Natural language question pattern",
                    "goal": "Brief goal description",
                    "steps": [
                        {"id": "step_id", "type": "api_call", "capability": "operation_id", "args": {}},
                    ],
                }
            ],
            "rules": [
                "Use $inputs.employee_id or $inputs.<param> for inputs",
                "Use $steps.<step_id> to reference previous step outputs",
                "Include diff/attribute_delta operators for comparison questions",
                "Include extract_field for simple lookups",
                "Keep plans concise (2-6 steps)",
            ],
        })

        try:
            result = client.converse_json(prompt, model_key="planning_model")
            if isinstance(result, list):
                for template in result:
                    if isinstance(template, dict) and "question_pattern" in template:
                        templates.append(template)
        except Exception:
            continue

    # Save templates
    out_dir = Path(config.get("index_file", "out/semantic_index")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "plan_templates.json", "w") as f:
        json.dump(templates, f, indent=2)

    # Embed question patterns for matching
    import faiss
    import boto3

    if not templates:
        print("No plan templates generated")
        return

    bedrock_config = config.get("bedrock", {})
    embed_client = boto3.client("bedrock-runtime", region_name=bedrock_config.get("region", "us-east-1"))
    model_id = bedrock_config.get("embedding_model", "amazon.titan-embed-text-v2:0")

    vectors = []
    for t in templates:
        vec = _embed(embed_client, model_id, t["question_pattern"])
        vectors.append(vec)

    dim = len(vectors[0])
    vecs_np = np.array(vectors, dtype=np.float32)
    faiss.normalize_L2(vecs_np)
    index = faiss.IndexFlatIP(dim)
    index.add(vecs_np)
    faiss.write_index(index, str(out_dir / "plan_templates.faiss"))

    print(f"Generated {len(templates)} plan templates -> {out_dir}/")


def match_cached_plan(question: str, config: dict[str, Any], threshold: float = 0.75) -> dict | None:
    """Try to match the question against pre-computed plan templates.

    Returns the plan if similarity exceeds threshold, None otherwise.
    """
    global _PLAN_TEMPLATES_CACHE

    index_dir = Path(config.get("index_file", "out/semantic_index")).with_suffix("")
    templates_path = index_dir / "plan_templates.json"
    faiss_path = index_dir / "plan_templates.faiss"

    if not templates_path.exists() or not faiss_path.exists():
        return None

    import faiss
    import boto3

    if _PLAN_TEMPLATES_CACHE is None:
        index = faiss.read_index(str(faiss_path))
        with open(templates_path) as f:
            templates = json.load(f)
        _PLAN_TEMPLATES_CACHE = (index, templates)
    else:
        index, templates = _PLAN_TEMPLATES_CACHE

    if not templates:
        return None

    bedrock_config = config.get("bedrock", {})
    client = boto3.client("bedrock-runtime", region_name=bedrock_config.get("region", "us-east-1"))
    model_id = bedrock_config.get("embedding_model", "amazon.titan-embed-text-v2:0")

    q_vector = np.array([_embed(client, model_id, question)], dtype=np.float32)
    faiss.normalize_L2(q_vector)

    scores, indices = index.search(q_vector, 1)
    if indices[0][0] < 0 or scores[0][0] < threshold:
        return None

    template = templates[indices[0][0]]
    return {
        "plan_id": "plan_cached",
        "question": question,
        "goal": template.get("goal", "answer_question"),
        "inputs": {},
        "steps": template.get("steps", []),
        "_cached": True,
        "_similarity": float(scores[0][0]),
    }


def _embed(client: Any, model_id: str, text: str) -> list[float]:
    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text[:8000]}),
    )
    body = json.loads(response["body"].read())
    return body["embedding"]
