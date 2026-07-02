"""
graph.queries — Reusable high-level query patterns over the code graph.

Every function takes a :class:`~graph.store.CodeGraph` as its first argument
and returns plain Python data structures suitable for serialisation or display.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .store import CodeGraph


# ------------------------------------------------------------------
# Hub detection
# ------------------------------------------------------------------

def hub_detection(graph: CodeGraph, top_n: int = 10) -> list[dict[str, Any]]:
    """Return the *top_n* most-connected nodes (highest total degree).

    Degree is computed as ``in-degree + out-degree`` across all edge types.

    Parameters
    ----------
    graph : CodeGraph
        A populated code graph.
    top_n : int
        Number of top hubs to return.

    Returns
    -------
    list[dict]
        Each dict contains the full node record plus ``"in_degree"``,
        ``"out_degree"``, and ``"total_degree"`` keys.
    """
    rows = graph._conn.execute(
        """
        WITH out_deg AS (
            SELECT from_name AS qn, COUNT(*) AS cnt
            FROM edges GROUP BY from_name
        ),
        in_deg AS (
            SELECT to_name AS qn, COUNT(*) AS cnt
            FROM edges GROUP BY to_name
        ),
        degrees AS (
            SELECT
                COALESCE(o.qn, i.qn) AS qualified_name,
                COALESCE(o.cnt, 0)    AS out_degree,
                COALESCE(i.cnt, 0)    AS in_degree
            FROM out_deg o
            FULL OUTER JOIN in_deg i ON o.qn = i.qn
        )
        SELECT
            n.*,
            d.in_degree,
            d.out_degree,
            (d.in_degree + d.out_degree) AS total_degree
        FROM degrees d
        JOIN nodes n ON n.qualified_name = d.qualified_name
        ORDER BY total_degree DESC
        LIMIT ?
        """,
        (top_n,),
    ).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        d = graph._row_to_dict(r)
        if d is not None:
            # The CTE columns are already in the row — just ensure they're ints
            d.setdefault("in_degree", 0)
            d.setdefault("out_degree", 0)
            d.setdefault("total_degree", 0)
            results.append(d)
    return results


# ------------------------------------------------------------------
# Orphan detection
# ------------------------------------------------------------------

def orphan_detection(graph: CodeGraph) -> list[dict[str, Any]]:
    """Return nodes that have **no** incoming *and* no outgoing edges.

    These are isolated code elements — potential dead code, unreferenced
    utilities, or simply nodes the parser hasn't linked yet.

    Returns
    -------
    list[dict]
        Orphan node dicts.
    """
    cur = graph._conn.execute(
        """
        SELECT n.* FROM nodes n
        WHERE n.qualified_name NOT IN (SELECT from_name FROM edges)
          AND n.qualified_name NOT IN (SELECT to_name   FROM edges)
        """
    )
    return graph._rows_to_list(cur)


# ------------------------------------------------------------------
# Domain summary
# ------------------------------------------------------------------

def domain_summary(graph: CodeGraph) -> dict[str, Any]:
    """Produce a per-domain breakdown of node counts and classifications.

    Returns
    -------
    dict
        ``{
            "<domain>": {
                "total": int,
                "by_kind": {"Function": int, …},
                "by_classification": {"BUSINESS_RULE": int, …}
            },
            …
        }``
        The special key ``"__unclassified__"`` holds nodes without a domain.
    """
    nodes = graph.all_nodes()

    domains: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "by_kind": defaultdict(int),
            "by_classification": defaultdict(int),
        }
    )

    for n in nodes:
        domain_key = n.get("domain") or "__unclassified__"
        bucket = domains[domain_key]
        bucket["total"] += 1
        bucket["by_kind"][n["kind"]] += 1
        cls = n.get("classification")
        if cls:
            bucket["by_classification"][cls] += 1

    # Convert nested defaultdicts to plain dicts for clean serialisation
    return {
        domain: {
            "total": info["total"],
            "by_kind": dict(info["by_kind"]),
            "by_classification": dict(info["by_classification"]),
        }
        for domain, info in sorted(domains.items())
    }


# ------------------------------------------------------------------
# Dependency depth
# ------------------------------------------------------------------

def dependency_depth(graph: CodeGraph, node_name: str) -> int:
    """Compute the maximum call-chain depth reachable from *node_name*.

    Performs a BFS over outgoing ``CALLS`` edges.  Cycles are handled
    gracefully (visited nodes are skipped).

    Parameters
    ----------
    graph : CodeGraph
        A populated code graph.
    node_name : str
        ``qualified_name`` of the starting node.

    Returns
    -------
    int
        Longest path length from *node_name* through ``CALLS`` edges.
        Returns ``0`` if the node has no outgoing calls.
    """
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(node_name, 0)])
    max_depth = 0

    while queue:
        current, depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        max_depth = max(max_depth, depth)

        # Only follow CALLS edges for dependency depth
        for row in graph._conn.execute(
            "SELECT to_name FROM edges WHERE from_name = ? AND edge_type = 'CALLS'",
            (current,),
        ):
            callee = row[0]
            if callee not in visited:
                queue.append((callee, depth + 1))

    return max_depth
