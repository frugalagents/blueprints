from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import networkx as nx


_GRAPH_CACHE: dict[str, tuple[float, nx.DiGraph]] = {}


def save_graph(graph: nx.DiGraph, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(nx.node_link_data(graph, edges="links"), f, indent=2)
    # Invalidate cache
    _GRAPH_CACHE.pop(str(p.resolve()), None)


def load_graph(path: str | Path) -> nx.DiGraph:
    p = Path(path)
    key = str(p.resolve())
    mtime = p.stat().st_mtime
    cached = _GRAPH_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(p) as f:
        data = json.load(f)
    graph = nx.node_link_graph(data, edges="links", directed=True)
    _GRAPH_CACHE[key] = (mtime, graph)
    return graph


def graph_stats(graph: nx.DiGraph) -> dict:
    node_types = Counter(d.get("node_type", "unknown") for _, d in graph.nodes(data=True))
    edge_types = Counter(d.get("edge_type", "unknown") for _, _, d in graph.edges(data=True))
    cap_nodes = [n for n, d in graph.nodes(data=True) if d.get("node_type") == "capability"]
    domain_counts = Counter(graph.nodes[n].get("domain") for n in cap_nodes)
    dep_edges = [(u, v, d) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "capability_depends_on"]
    cross_deps = [e for e in dep_edges if e[2].get("is_cross_domain")]
    return {
        "total_nodes": len(graph.nodes),
        "total_edges": len(graph.edges),
        "nodes_by_type": dict(node_types),
        "edges_by_type": dict(edge_types),
        "capabilities_per_domain": dict(domain_counts),
        "dependency_edges": len(dep_edges),
        "cross_domain_dependencies": len(cross_deps),
    }


def capability_nodes(graph: nx.DiGraph) -> list[tuple[str, dict]]:
    return [(n, d) for n, d in graph.nodes(data=True) if d.get("node_type") == "capability"]
