from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx

from api_kg.graph.graph_store import load_graph
from api_kg.semantic.embedder import search_semantic


def retrieve(question: str, graph_file: str | Path, max_capabilities: int = 10, graph_hops: int = 2, config: dict[str, Any] | None = None) -> dict:
    graph = load_graph(graph_file)

    # Step 1: Semantic search over description embeddings
    vector_hits = []
    if config:
        vector_hits = search_semantic(question, config, top_k=max_capabilities * 2)

    if not vector_hits:
        return _empty_result(question, graph)

    # Filter out mutations for read queries
    q_words = set(question.lower().split())
    is_action = bool(q_words & {"create", "submit", "update", "delete", "approve", "reject", "cancel", "modify"})
    if not is_action:
        vector_hits = [h for h in vector_hits if _is_read_op(graph, h["operation_id"])]

    # Step 2: Graph expansion — only expand from HIGH-confidence vector hits
    # Use top 5 as "anchor" seeds, expand only from those
    anchor_nodes = [h["operation_id"] for h in vector_hits[:5]]
    expanded = _graph_expand(graph, anchor_nodes, hops=graph_hops)

    # Filter expanded set to only capabilities, remove mutations if needed
    expanded_caps = set()
    for node in expanded:
        if not graph.has_node(node):
            continue
        if graph.nodes[node].get("node_type") != "capability":
            continue
        if not is_action and graph.nodes[node].get("side_effect") == "write":
            continue
        expanded_caps.add(node)

    # Also include remaining vector hits (even if not graph-connected)
    for h in vector_hits[:max_capabilities]:
        node = h["operation_id"]
        if graph.has_node(node) and graph.nodes[node].get("node_type") == "capability":
            if is_action or graph.nodes[node].get("side_effect") != "write":
                expanded_caps.add(node)

    # Step 3: Score using graph structure
    # - Dependency proximity to anchor nodes (direct connection = boost)
    # - Community coherence (same community as top hits = boost)
    vector_scores = {h["operation_id"]: h["score"] for h in vector_hits}
    anchor_set = set(anchor_nodes)
    # Use only top 3 vector hits to determine the intent community
    top_intent_nodes = set(h["operation_id"] for h in vector_hits[:3])
    anchor_communities = _get_node_communities(graph, top_intent_nodes)

    final_scores: dict[str, float] = {}
    for node in expanded_caps:
        vec_score = vector_scores.get(node, 0.0)
        proximity = _graph_proximity_score(graph, node, anchor_set)
        community_bonus = _community_coherence(graph, node, anchor_communities)
        final_scores[node] = vec_score + proximity + community_bonus

    # Step 4: Rank and build call sequence
    ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)[:max_capabilities]
    selected = {n for n, _ in ranked}
    call_sequence = _topological_order(graph, selected)

    # Build output (score-ordered for plan generator, topo-ordered for execution)
    score_ordered = [n for n, _ in ranked]
    caps = [dict(graph.nodes[n]) for n in score_ordered if graph.has_node(n)]
    domains = sorted({c.get("domain") for c in caps if c.get("domain")})
    edges = _subgraph_edges(graph, selected)

    return {
        "question": question,
        "matched_capabilities": caps,
        "call_sequence": call_sequence,
        "domains": domains,
        "cross_domain_paths": [e for e in edges if e.get("is_cross_domain")],
        "edges": edges,
        "summary": f"Found {len(caps)} capabilities across {len(domains)} domains: {', '.join(domains)}",
        "metadata": {
            "total_graph_nodes": len(graph.nodes),
            "returned_capabilities": len(caps),
            "retrieval_method": "semantic_search_plus_graph_expansion",
            "vector_hits": len(vector_hits),
            "expanded_from_graph": len(expanded_caps) - len(anchor_nodes),
        },
    }


def _is_read_op(graph: nx.DiGraph, node: str) -> bool:
    if not graph.has_node(node):
        return True
    return graph.nodes[node].get("side_effect") != "write"


def _graph_expand(graph: nx.DiGraph, seed_nodes: list[str], hops: int) -> set[str]:
    """Expand seed nodes along dependency edges in the graph."""
    expanded = set(seed_nodes)
    frontier = set(seed_nodes)

    for _ in range(hops):
        new_frontier: set[str] = set()
        for node in frontier:
            if not graph.has_node(node):
                continue
            # Follow dependency edges (both directions)
            for neighbor in graph.predecessors(node):
                edge = graph.get_edge_data(neighbor, node, {})
                if edge.get("edge_type") == "capability_depends_on":
                    if graph.nodes.get(neighbor, {}).get("node_type") == "capability":
                        new_frontier.add(neighbor)

            for neighbor in graph.successors(node):
                edge = graph.get_edge_data(node, neighbor, {})
                if edge.get("edge_type") == "capability_depends_on":
                    if graph.nodes.get(neighbor, {}).get("node_type") == "capability":
                        new_frontier.add(neighbor)

        new_nodes = new_frontier - expanded
        if not new_nodes:
            break
        expanded.update(new_nodes)
        frontier = new_nodes

    return expanded


def _graph_proximity_score(graph: nx.DiGraph, node: str, anchors: set[str]) -> float:
    """Score based on dependency-edge distance to the anchor cluster.

    Only counts actual dependency edges (capability_depends_on) — not shared
    parameters, which connect everything via employee_id and create false proximity.
    """
    if not graph.has_node(node):
        return 0.0
    if node in anchors:
        return 0.5

    min_distance = float("inf")
    for anchor in anchors:
        if not graph.has_node(anchor):
            continue
        # Direct dependency edge (distance 1)
        edge_fwd = graph.get_edge_data(node, anchor)
        edge_rev = graph.get_edge_data(anchor, node)
        if (edge_fwd and edge_fwd.get("edge_type") == "capability_depends_on") or \
           (edge_rev and edge_rev.get("edge_type") == "capability_depends_on"):
            min_distance = min(min_distance, 1)
            continue

        # 2-hop via shared dependency neighbor (only through dependency edges)
        node_dep_neighbors = set()
        for n in graph.successors(node):
            if graph.get_edge_data(node, n, {}).get("edge_type") == "capability_depends_on":
                node_dep_neighbors.add(n)
        for n in graph.predecessors(node):
            if graph.get_edge_data(n, node, {}).get("edge_type") == "capability_depends_on":
                node_dep_neighbors.add(n)

        anchor_dep_neighbors = set()
        for n in graph.successors(anchor):
            if graph.get_edge_data(anchor, n, {}).get("edge_type") == "capability_depends_on":
                anchor_dep_neighbors.add(n)
        for n in graph.predecessors(anchor):
            if graph.get_edge_data(n, anchor, {}).get("edge_type") == "capability_depends_on":
                anchor_dep_neighbors.add(n)

        if node_dep_neighbors & anchor_dep_neighbors:
            min_distance = min(min_distance, 2)

    if min_distance == float("inf"):
        return 0.0
    return 0.4 / min_distance


def _get_node_communities(graph: nx.DiGraph, nodes: set[str]) -> set[str]:
    """Get the community/domain cluster that anchor nodes belong to."""
    communities = set()
    for node in nodes:
        if graph.has_node(node):
            communities.add(graph.nodes[node].get("domain", ""))
    return communities


def _community_coherence(graph: nx.DiGraph, node: str, anchor_communities: set[str]) -> float:
    """Boost nodes in the same domain community as the anchor cluster."""
    if not graph.has_node(node):
        return 0.0
    node_domain = graph.nodes[node].get("domain", "")
    if node_domain in anchor_communities:
        return 0.15
    return -0.1  # Penalize nodes outside the anchor community


def _topological_order(graph: nx.DiGraph, nodes: set[str]) -> list[str]:
    sub = graph.subgraph(nodes).copy()
    # Remove non-dependency edges for clean ordering
    edges_to_remove = [(u, v) for u, v, d in sub.edges(data=True) if d.get("edge_type") != "capability_depends_on"]
    sub.remove_edges_from(edges_to_remove)
    try:
        ordered = list(nx.topological_sort(sub))
    except nx.NetworkXUnfeasible:
        ordered = list(nodes)
    return [n for n in ordered if graph.nodes.get(n, {}).get("node_type") == "capability"]


def _subgraph_edges(graph: nx.DiGraph, nodes: set[str]) -> list[dict]:
    edges = []
    for u, v, data in graph.edges(data=True):
        if u in nodes and v in nodes and data.get("edge_type") == "capability_depends_on":
            edges.append({
                "from": u,
                "to": v,
                "from_domain": graph.nodes[u].get("domain"),
                "to_domain": graph.nodes[v].get("domain"),
                **data,
            })
    return edges


def _empty_result(question: str, graph: nx.DiGraph) -> dict:
    return {
        "question": question,
        "matched_capabilities": [],
        "call_sequence": [],
        "domains": [],
        "cross_domain_paths": [],
        "edges": [],
        "summary": "No semantic index found. Run 'describe' and 'index' first.",
        "metadata": {"total_graph_nodes": len(graph.nodes), "returned_capabilities": 0, "retrieval_method": "none"},
    }
