from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api_kg.graph.graph_store import capability_nodes, load_graph


def build_index(graph_file: str | Path, config: dict[str, Any]) -> None:
    import faiss
    import numpy as np
    import boto3

    graph = load_graph(graph_file)
    bedrock_config = config.get("bedrock", {})
    client = boto3.client("bedrock-runtime", region_name=bedrock_config.get("region", "us-east-1"))
    model_id = bedrock_config.get("embedding_model", "amazon.titan-embed-text-v2:0")

    entries = []
    vectors = []
    for node_id, data in capability_nodes(graph):
        text = _build_text(node_id, data)
        vector = _embed(client, model_id, text)
        entries.append({"operation_id": node_id, "text": text})
        vectors.append(vector)

    # Build FAISS index
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

    # Save index and metadata
    index_dir = Path(config.get("index_file", "out/vector_index")).with_suffix("")
    index_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_dir / "index.faiss"))
    with open(index_dir / "metadata.json", "w") as f:
        json.dump({"model_id": model_id, "dim": dim, "entries": entries}, f)

    print(f"Indexed {len(entries)} capabilities ({dim}d) -> {index_dir}/")


_INDEX_CACHE: dict[str, tuple[Any, list[dict]]] = {}


def query_index(question: str, config: dict[str, Any], top_k: int = 20) -> list[dict[str, Any]]:
    index_dir = Path(config.get("index_file", "out/vector_index")).with_suffix("")
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
        results.append({"operation_id": entries[idx]["operation_id"], "score": float(score)})
    return results


def _build_text(node_id: str, data: dict) -> str:
    parts = [
        node_id.replace("_", " "),
        data.get("domain", ""),
        data.get("summary", ""),
        data.get("description", ""),
        data.get("path", ""),
        " ".join(data.get("produces_entities", [])),
        " ".join(data.get("consumes_entities", [])),
        " ".join(f.get("name", "").replace("_", " ") for f in data.get("input_fields", [])),
        " ".join(f.get("name", "").replace("_", " ") for f in data.get("output_fields", [])),
    ]
    return " ".join(p for p in parts if p)


def _embed(client: Any, model_id: str, text: str) -> list[float]:
    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text}),
    )
    body = json.loads(response["body"].read())
    return body["embedding"]
