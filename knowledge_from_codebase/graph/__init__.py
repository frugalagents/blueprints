"""
graph — Code-graph storage, traversal, community detection, and query toolkit.

Primary exports:

- :class:`CodeGraph` — SQLite-backed directed code graph with FTS5 search.
- :func:`detect_communities` — Louvain / fallback community detection.

Quick start::

    from graph import CodeGraph, detect_communities

    g = CodeGraph(":memory:")
    g.add_node("myapp.billing.calculate_tax", "calculate_tax", "Function",
               file="myapp/billing.py", start_line=10, end_line=25)
    g.add_node("myapp.billing.apply_discount", "apply_discount", "Function",
               file="myapp/billing.py", start_line=27, end_line=40)
    g.add_edge("myapp.billing.calculate_tax", "myapp.billing.apply_discount", "CALLS")

    communities = detect_communities(g)
"""

from .store import CodeGraph
from .community import detect_communities, assign_communities
from .schema import (
    NODE_TYPES,
    EDGE_TYPES,
    CLASSIFICATIONS,
    Node,
    Edge,
    Community,
)
from .queries import (
    hub_detection,
    orphan_detection,
    domain_summary,
    dependency_depth,
)

__all__ = [
    # Core
    "CodeGraph",
    # Community
    "detect_communities",
    "assign_communities",
    # Schema types
    "Node",
    "Edge",
    "Community",
    "NODE_TYPES",
    "EDGE_TYPES",
    "CLASSIFICATIONS",
    # Queries
    "hub_detection",
    "orphan_detection",
    "domain_summary",
    "dependency_depth",
]
