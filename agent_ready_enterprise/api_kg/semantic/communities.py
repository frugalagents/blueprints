from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx
import yaml

from api_kg.graph.graph_store import load_graph


def detect_communities(graph_file: str | Path) -> dict[str, list[str]]:
    """Detect API capability communities using Louvain method."""
    graph = load_graph(graph_file)

    # Build undirected capability subgraph for community detection
    cap_nodes = [n for n, d in graph.nodes(data=True) if d.get("node_type") == "capability"]
    cap_graph = nx.Graph()

    for node in cap_nodes:
        cap_graph.add_node(node, domain=graph.nodes[node].get("domain", ""))

    # Add edges between capabilities that share dependencies or entities
    for u, v, data in graph.edges(data=True):
        if u in cap_nodes and v in cap_nodes:
            weight = data.get("confidence", 0.5)
            if data.get("is_cross_domain"):
                weight *= 1.5
            cap_graph.add_edge(u, v, weight=weight)

    # Also connect capabilities that share parameter nodes
    param_to_caps: dict[str, list[str]] = {}
    for node in cap_nodes:
        for pred in graph.predecessors(node):
            if graph.nodes.get(pred, {}).get("node_type") == "parameter":
                if graph.nodes[pred].get("is_identifier"):
                    param_to_caps.setdefault(pred, []).append(node)
        for succ in graph.successors(node):
            if graph.nodes.get(succ, {}).get("node_type") == "parameter":
                if graph.nodes[succ].get("is_identifier"):
                    param_to_caps.setdefault(succ, []).append(node)

    for param, caps in param_to_caps.items():
        for i in range(len(caps)):
            for j in range(i + 1, len(caps)):
                if cap_graph.has_edge(caps[i], caps[j]):
                    cap_graph[caps[i]][caps[j]]["weight"] += 0.3
                else:
                    cap_graph.add_edge(caps[i], caps[j], weight=0.3)

    # Louvain community detection
    communities = nx.community.louvain_communities(cap_graph, weight="weight", resolution=1.0)

    result = {}
    for i, community in enumerate(communities):
        # Name community by most common domain
        domains = [cap_graph.nodes[n].get("domain", "") for n in community]
        from collections import Counter
        domain_counts = Counter(domains)
        name = domain_counts.most_common(1)[0][0] if domain_counts else f"cluster_{i}"
        if name in result:
            name = f"{name}_{i}"
        result[name] = sorted(community)

    return result


def generate_community_summaries(
    graph_file: str | Path,
    config: dict[str, Any],
    output_file: str | Path = "semantic/communities.yaml",
) -> None:
    """Generate LLM summaries for each community cluster."""
    graph = load_graph(graph_file)
    communities = detect_communities(graph_file)

    summaries = []
    for community_name, members in communities.items():
        # Gather capability info for this community
        cap_infos = []
        for cap_id in members:
            if graph.has_node(cap_id):
                data = graph.nodes[cap_id]
                cap_infos.append({
                    "operation_id": cap_id,
                    "domain": data.get("domain", ""),
                    "summary": data.get("summary", ""),
                })

        summary = {
            "community": community_name,
            "capabilities": [c["operation_id"] for c in cap_infos],
            "domains": sorted(set(c["domain"] for c in cap_infos)),
            "size": len(members),
            "description": _basic_community_description(community_name, cap_infos),
        }
        summaries.append(summary)

    # Try LLM enrichment
    try:
        from api_kg.llm.bedrock_client import BedrockClient
        client = BedrockClient(config.get("bedrock", {}))
        for summary in summaries:
            prompt = f"""Summarize what this cluster of API capabilities does in 2-3 sentences. Be specific about business functions.

Cluster: {summary['community']}
Domains: {summary['domains']}
Capabilities: {json.dumps([{'id': c['operation_id'], 'summary': c['summary']} for c in cap_infos if c['operation_id'] in summary['capabilities']])}

Return only the summary text."""
            try:
                summary["description"] = client.converse_text(prompt, model_key="synthesis_model")
            except Exception:
                pass
    except Exception:
        pass

    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        yaml.dump({"communities": summaries}, f, default_flow_style=False, sort_keys=False)

    print(f"Detected {len(summaries)} communities -> {out}")


def _basic_community_description(name: str, cap_infos: list[dict]) -> str:
    summaries = [c["summary"] for c in cap_infos if c.get("summary")][:5]
    return f"Cluster '{name}' contains {len(cap_infos)} capabilities: {'; '.join(summaries)}"
