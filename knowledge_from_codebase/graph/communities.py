"""
graph.communities — Pipeline-facing wrapper for community detection.

Provides :func:`detect_communities` with the ``(db_path, verbose)``
signature that ``main.py`` expects.  Delegates to
:func:`graph.community.detect_communities` and
:func:`graph.community.assign_communities`.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Dict

from .store import CodeGraph
from .community import (
    detect_communities as _detect,
    assign_communities as _assign,
)

logger = logging.getLogger(__name__)


def detect_communities(*, db_path: str, verbose: bool = False) -> None:
    """Run community detection and write results back to the database.

    After assigning community IDs to nodes, this function also updates
    the ``functions`` compatibility table and auto-labels each community
    with a domain name derived from the most common file paths.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database (must already contain nodes/edges).
    verbose : bool
        Log progress details.
    """
    graph = CodeGraph(db_path)

    try:
        # 1. Detect communities
        communities = _detect(graph)
        if verbose:
            logger.info("Detected %d communities", len(communities))

        if not communities:
            logger.info("No communities detected (graph may be empty)")
            return

        # 2. Assign community IDs + create MEMBER_OF edges
        _assign(graph, communities)

        # 3. Auto-label communities with domain names
        _label_communities(graph, communities, verbose=verbose)

        # 4. Sync community_id and domain back to the functions compat table
        _sync_functions_table(graph)

        if verbose:
            logger.info("Community detection complete")
    finally:
        graph.close()


def _label_communities(
    graph: CodeGraph,
    communities: Dict[int, list[str]],
    *,
    verbose: bool = False,
) -> None:
    """Derive a human-readable domain label for each community.

    Strategy: look at the file paths of member nodes and pick the most
    common top-level directory or second-level directory as the label.
    """
    for cid, members in communities.items():
        # Gather file paths for all members
        path_parts: list[str] = []
        for qname in members:
            node = graph.get_node(qname)
            if node and node.get("file"):
                parts = PurePosixPath(node["file"]).parts
                # Use second-level dir if available, else first
                if len(parts) >= 2:
                    path_parts.append(parts[0] + "/" + parts[1])
                elif parts:
                    path_parts.append(parts[0])

        if not path_parts:
            label = f"cluster_{cid}"
        else:
            # Most common path component
            counter = Counter(path_parts)
            label = counter.most_common(1)[0][0]
            # Clean up: remove file extensions, replace separators
            label = (
                label.replace("/", ".")
                .replace("\\", ".")
                .replace(".py", "")
                .replace("_", " ")
                .title()
            )

        # Write domain label to all member nodes
        for qname in members:
            graph._conn.execute(
                "UPDATE nodes SET domain = ? WHERE qualified_name = ? AND domain IS NULL",
                (label, qname),
            )

        if verbose:
            logger.info("Community %d → domain '%s' (%d members)", cid, label, len(members))

    graph._conn.commit()


def _sync_functions_table(graph: CodeGraph) -> None:
    """Copy community_id and domain from nodes → functions compat table."""
    graph._conn.execute(
        """
        UPDATE functions
        SET community_id = (
                SELECT n.community_id FROM nodes n
                WHERE n.qualified_name = functions.qualified_name
            ),
            domain = (
                SELECT n.domain FROM nodes n
                WHERE n.qualified_name = functions.qualified_name
            )
        """
    )
    graph._conn.commit()
