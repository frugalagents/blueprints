"""
graph.community — Community detection for the code graph.

Uses NetworkX with the Louvain algorithm (``community_louvain`` from the
``python-louvain`` package) when available, falling back to a simple
directory-based grouping otherwise.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from .store import CodeGraph

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def detect_communities(graph: CodeGraph) -> dict[int, list[str]]:
    """Detect communities (clusters) of related code nodes.

    Attempts Louvain community detection via NetworkX + python-louvain.
    If those packages are unavailable, falls back to grouping nodes by
    their top-level directory.

    Parameters
    ----------
    graph : CodeGraph
        The populated code graph.

    Returns
    -------
    dict[int, list[str]]
        Mapping from community ID to a list of ``qualified_name`` values.
    """
    try:
        return _louvain_communities(graph)
    except ImportError:
        logger.info(
            "python-louvain or networkx not available — "
            "falling back to directory-based grouping"
        )
        return _directory_communities(graph)


def assign_communities(
    graph: CodeGraph,
    communities: dict[int, list[str]],
) -> None:
    """Write community assignments back into the graph.

    For every ``(community_id, members)`` pair the corresponding node rows
    are updated **and** a ``Community`` meta-node is created with
    ``MEMBER_OF`` edges from each member.

    Parameters
    ----------
    graph : CodeGraph
        The code graph to update.
    communities : dict[int, list[str]]
        Output of :func:`detect_communities`.
    """
    for cid, members in communities.items():
        # Update community_id on every member node
        for qname in members:
            graph._conn.execute(
                "UPDATE nodes SET community_id = ? WHERE qualified_name = ?",
                (cid, qname),
            )

        # Create a synthetic Community node
        community_qname = f"__community__{cid}"
        graph.add_node(
            qualified_name=community_qname,
            name=f"Community {cid}",
            kind="Community",
            file="",
            start_line=0,
            end_line=0,
            docstring=f"Auto-detected community with {len(members)} members.",
            source_snippet="",
            metadata={"member_count": len(members)},
        )

        # Create MEMBER_OF edges
        for qname in members:
            graph.add_edge(qname, community_qname, "MEMBER_OF")

    graph._conn.commit()
    logger.info(
        "Assigned %d communities covering %d nodes",
        len(communities),
        sum(len(m) for m in communities.values()),
    )


# ------------------------------------------------------------------
# Louvain-based detection
# ------------------------------------------------------------------

def _louvain_communities(graph: CodeGraph) -> dict[int, list[str]]:
    """Use NetworkX + python-louvain for community detection.

    Raises :class:`ImportError` if either library is missing so the
    caller can fall back gracefully.
    """
    import networkx as nx  # type: ignore[import-untyped]

    try:
        import community as community_louvain  # type: ignore[import-untyped]
    except ImportError:
        # Some installs expose it as `community.community_louvain`
        from community import community_louvain  # type: ignore[import-untyped,no-redef]

    # Build an undirected NetworkX graph from edges
    G = nx.Graph()

    nodes = graph.all_nodes()
    for n in nodes:
        G.add_node(n["qualified_name"])

    edges = graph.all_edges()
    for e in edges:
        # Weight: CALLS edges are heavier than IMPORTS to encourage
        # grouping tightly-coupled code together.
        weight = 2.0 if e["edge_type"] == "CALLS" else 1.0
        if G.has_edge(e["from_name"], e["to_name"]):
            G[e["from_name"]][e["to_name"]]["weight"] += weight
        else:
            G.add_edge(e["from_name"], e["to_name"], weight=weight)

    if len(G) == 0:
        return {}

    # Run Louvain
    partition: dict[str, int] = community_louvain.best_partition(
        G, weight="weight", random_state=42
    )

    # Invert: community_id → [qualified_name, …]
    communities: dict[int, list[str]] = defaultdict(list)
    for qname, cid in partition.items():
        communities[cid].append(qname)

    logger.info("Louvain detected %d communities", len(communities))
    return dict(communities)


# ------------------------------------------------------------------
# Directory-based fallback
# ------------------------------------------------------------------

def _directory_communities(graph: CodeGraph) -> dict[int, list[str]]:
    """Group nodes by their top-level source directory.

    This is a simple heuristic that works without any external
    dependencies: files sharing the same top-level package directory are
    assumed to belong to the same community.
    """
    nodes = graph.all_nodes()

    dir_groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        file_path = n.get("file", "")
        if file_path:
            # Use the first path component as the group key
            parts = PurePosixPath(file_path).parts
            group_key = parts[0] if parts else "__root__"
        else:
            group_key = "__unknown__"
        dir_groups[group_key].append(n["qualified_name"])

    # Assign sequential integer IDs
    communities: dict[int, list[str]] = {}
    for idx, (_, members) in enumerate(sorted(dir_groups.items())):
        communities[idx] = members

    logger.info(
        "Directory-based grouping produced %d communities", len(communities)
    )
    return communities
