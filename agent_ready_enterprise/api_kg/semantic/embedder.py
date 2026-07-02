from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from api_kg.semantic.descriptions import load_descriptions


_INDEX_CACHE: dict[str, tuple[Any, list[dict]]] = {}


def build_semantic_index(semantic_dir: str | Path, config: dict[str, Any]) -> None:
    import faiss
    import numpy as np
    import boto3

    descriptions = load_descriptions(semantic_dir)
    if not descriptions:
        raise ValueError(f"No descriptions found in {semantic_dir}. Run 'describe' first.")

    bedrock_config = config.get("bedrock", {})
    client = boto3.client("bedrock-runtime", region_name=bedrock_config.get("region", "us-east-1"))
    model_id = bedrock_config.get("embedding_model", "amazon.titan-embed-text-v2:0")

    entries = []
    vectors = []
    for desc in descriptions:
        text = _description_to_text(desc)
        vector = _embed(client, model_id, text)
        entries.append({
            "operation_id": desc["capability"],
            "domain": desc.get("domain", ""),
            "text": text,
        })
        vectors.append(vector)

    dim = len(vectors[0])
    vecs_np = np.array(vectors, dtype=np.float32)
    faiss.normalize_L2(vecs_np)

    if len(vectors) < 500:
        index = faiss.IndexFlatIP(dim)
    else:
        nlist = min(int(len(vectors) ** 0.5), 100)
        quantizer = faiss.IndexFlatIP(dim)
        index = faiss.IndexIVFFlat(quantizer, dim, nlist, faiss.METRIC_INNER_PRODUCT)
        index.train(vecs_np)

    index.add(vecs_np)

    index_dir = Path(config.get("index_file", "out/semantic_index")).with_suffix("")
    index_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_dir / "index.faiss"))
    with open(index_dir / "metadata.json", "w") as f:
        json.dump({"model_id": model_id, "dim": dim, "entries": entries}, f)

    # Invalidate cache
    _INDEX_CACHE.pop(str(index_dir), None)
    print(f"Indexed {len(entries)} capability descriptions ({dim}d) -> {index_dir}/")


def search_semantic(question: str, config: dict[str, Any], top_k: int = 20) -> list[dict[str, Any]]:
    index_dir = Path(config.get("index_file", "out/semantic_index")).with_suffix("")
    meta_path = index_dir / "metadata.json"
    if not meta_path.exists():
        return []

    import faiss
    import numpy as np
    import boto3

    cache_key = str(index_dir)
    if cache_key in _INDEX_CACHE:
        index, entries = _INDEX_CACHE[cache_key]
    else:
        index = faiss.read_index(str(index_dir / "index.faiss"))
        with open(meta_path) as f:
            meta = json.load(f)
        entries = meta["entries"]
        _INDEX_CACHE[cache_key] = (index, entries)

    bedrock_config = config.get("bedrock", {})
    client = boto3.client("bedrock-runtime", region_name=bedrock_config.get("region", "us-east-1"))
    model_id = bedrock_config.get("embedding_model", "amazon.titan-embed-text-v2:0")

    q_vector = np.array([_embed(client, model_id, question)], dtype=np.float32)
    faiss.normalize_L2(q_vector)

    k = min(top_k, len(entries))
    scores, indices = index.search(q_vector, k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0:
            continue
        results.append({
            "operation_id": entries[idx]["operation_id"],
            "domain": entries[idx].get("domain", ""),
            "score": float(score),
        })
    return results


def _description_to_text(desc: dict) -> str:
    """Convert a semantic description into embeddable text.

    Embeds the full semantic content — descriptions, use cases, relationships —
    not just field names or operation IDs.
    """
    parts = [
        desc.get("summary", ""),
        desc.get("description", ""),
    ]
    if desc.get("use_cases"):
        parts.append("Use cases: " + "; ".join(desc["use_cases"]))
    if desc.get("produces"):
        parts.append("Produces: " + ", ".join(str(p) for p in desc["produces"]))
    if desc.get("preconditions"):
        parts.append("Requires: " + ", ".join(str(p) for p in desc["preconditions"]))
    if desc.get("related"):
        related_strs = []
        for r in desc["related"]:
            if isinstance(r, dict):
                related_strs.append(f"{r.get('capability', '')}: {r.get('reason', '')}")
            elif isinstance(r, str):
                related_strs.append(r)
        if related_strs:
            parts.append("Related: " + "; ".join(related_strs))
    return " ".join(p for p in parts if p)


def _embed(client: Any, model_id: str, text: str) -> list[float]:
    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text[:8000]}),
    )
    body = json.loads(response["body"].read())
    return body["embedding"]
