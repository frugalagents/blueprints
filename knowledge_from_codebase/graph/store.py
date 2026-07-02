"""
graph.store — SQLite-backed code graph storage with FTS5 full-text search.

The :class:`CodeGraph` class persists nodes and edges in a local SQLite
database and exposes traversal, search, classification update, and export
methods.  It is the central data structure for the CodebaseBusinessExtractor
pipeline.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

from .schema import EDGE_TYPES, NODE_TYPES, CLASSIFICATIONS


class CodeGraph:
    """SQLite-backed directed code graph.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.  Use ``":memory:"`` for a
        transient in-memory graph.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, db_path: str = "code_graph.db") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    # ------------------------------------------------------------------
    # Schema creation
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        """Create tables, indexes, and FTS virtual table if they don't exist."""
        cur = self._conn.cursor()

        cur.executescript(
            """
            -- --------------------------------------------------------
            -- Core tables
            -- --------------------------------------------------------
            CREATE TABLE IF NOT EXISTS nodes (
                qualified_name  TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                kind            TEXT NOT NULL,
                file            TEXT NOT NULL DEFAULT '',
                start_line      INTEGER NOT NULL DEFAULT 0,
                end_line        INTEGER NOT NULL DEFAULT 0,
                docstring       TEXT NOT NULL DEFAULT '',
                source_snippet  TEXT NOT NULL DEFAULT '',
                domain          TEXT,
                classification  TEXT,
                business_summary TEXT,
                confidence      REAL,
                community_id    INTEGER,
                metadata_json   TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS edges (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_name   TEXT NOT NULL,
                to_name     TEXT NOT NULL,
                edge_type   TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(from_name, to_name, edge_type)
            );

            -- --------------------------------------------------------
            -- Performance indexes
            -- --------------------------------------------------------
            CREATE INDEX IF NOT EXISTS idx_nodes_kind
                ON nodes(kind);
            CREATE INDEX IF NOT EXISTS idx_nodes_classification
                ON nodes(classification);
            CREATE INDEX IF NOT EXISTS idx_nodes_domain
                ON nodes(domain);
            CREATE INDEX IF NOT EXISTS idx_nodes_community
                ON nodes(community_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_file
                ON nodes(file);

            CREATE INDEX IF NOT EXISTS idx_edges_from
                ON edges(from_name);
            CREATE INDEX IF NOT EXISTS idx_edges_to
                ON edges(to_name);
            CREATE INDEX IF NOT EXISTS idx_edges_type
                ON edges(edge_type);
            CREATE INDEX IF NOT EXISTS idx_edges_from_type
                ON edges(from_name, edge_type);
            CREATE INDEX IF NOT EXISTS idx_edges_to_type
                ON edges(to_name, edge_type);
            """
        )

        # FTS5 virtual table — created outside executescript because
        # IF NOT EXISTS isn't supported for virtual tables in all SQLite
        # versions; we catch the "already exists" error instead.
        try:
            cur.execute(
                """
                CREATE VIRTUAL TABLE nodes_fts USING fts5(
                    qualified_name,
                    name,
                    docstring,
                    business_summary,
                    content=nodes,
                    content_rowid=rowid
                )
                """
            )
        except sqlite3.OperationalError:
            pass  # already exists

        # Triggers to keep FTS in sync with the nodes table.
        for trigger_sql in [
            """
            CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
                INSERT INTO nodes_fts(rowid, qualified_name, name, docstring, business_summary)
                VALUES (new.rowid, new.qualified_name, new.name, new.docstring, new.business_summary);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
                INSERT INTO nodes_fts(nodes_fts, rowid, qualified_name, name, docstring, business_summary)
                VALUES ('delete', old.rowid, old.qualified_name, old.name, old.docstring, old.business_summary);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
                INSERT INTO nodes_fts(nodes_fts, rowid, qualified_name, name, docstring, business_summary)
                VALUES ('delete', old.rowid, old.qualified_name, old.name, old.docstring, old.business_summary);
                INSERT INTO nodes_fts(rowid, qualified_name, name, docstring, business_summary)
                VALUES (new.rowid, new.qualified_name, new.name, new.docstring, new.business_summary);
            END
            """,
        ]:
            try:
                cur.execute(trigger_sql)
            except sqlite3.OperationalError:
                pass

        self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """Convert a :class:`sqlite3.Row` to a plain dict, deserialising JSON."""
        if row is None:
            return None
        d = dict(row)
        if "metadata_json" in d:
            d["metadata"] = json.loads(d.pop("metadata_json"))
        return d

    def _rows_to_list(self, cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
        return [self._row_to_dict(r) for r in cursor.fetchall()]  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(
        self,
        qualified_name: str,
        name: str,
        kind: str,
        file: str = "",
        start_line: int = 0,
        end_line: int = 0,
        docstring: str = "",
        source_snippet: str = "",
        domain: Optional[str] = None,
        classification: Optional[str] = None,
        business_summary: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Insert or update (upsert) a node.

        Parameters
        ----------
        qualified_name : str
            Unique fully-qualified name used as primary key.
        kind : str
            Must be one of :pydata:`NODE_TYPES`.
        classification : str | None
            Must be one of :pydata:`CLASSIFICATIONS` or ``None``.
        confidence : float | None
            Value in ``[0.0, 1.0]`` or ``None``.
        metadata : dict | None
            Arbitrary JSON-serialisable extra data.
        """
        if kind not in NODE_TYPES:
            raise ValueError(f"Invalid node kind {kind!r}. Must be one of {NODE_TYPES}")
        if classification is not None and classification not in CLASSIFICATIONS:
            raise ValueError(
                f"Invalid classification {classification!r}. "
                f"Must be one of {CLASSIFICATIONS}"
            )
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence must be in [0.0, 1.0], got {confidence}")

        meta_json = json.dumps(metadata or {})

        self._conn.execute(
            """
            INSERT INTO nodes (
                qualified_name, name, kind, file, start_line, end_line,
                docstring, source_snippet, domain, classification,
                business_summary, confidence, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(qualified_name) DO UPDATE SET
                name             = excluded.name,
                kind             = excluded.kind,
                file             = excluded.file,
                start_line       = excluded.start_line,
                end_line         = excluded.end_line,
                docstring        = excluded.docstring,
                source_snippet   = excluded.source_snippet,
                domain           = excluded.domain,
                classification   = excluded.classification,
                business_summary = excluded.business_summary,
                confidence       = excluded.confidence,
                metadata_json    = excluded.metadata_json
            """,
            (
                qualified_name, name, kind, file, start_line, end_line,
                docstring, source_snippet, domain, classification,
                business_summary, confidence, meta_json,
            ),
        )
        self._conn.commit()

    def get_node(self, qualified_name: str) -> dict[str, Any] | None:
        """Retrieve a single node by its qualified name."""
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE qualified_name = ?", (qualified_name,)
        ).fetchone()
        return self._row_to_dict(row)

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(
        self,
        from_name: str,
        to_name: str,
        edge_type: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create a directed edge (relationship) between two nodes.

        Duplicate ``(from_name, to_name, edge_type)`` triples are silently
        ignored (INSERT OR IGNORE).

        Parameters
        ----------
        edge_type : str
            Must be one of :pydata:`EDGE_TYPES`.
        """
        if edge_type not in EDGE_TYPES:
            raise ValueError(
                f"Invalid edge type {edge_type!r}. Must be one of {EDGE_TYPES}"
            )
        meta_json = json.dumps(metadata or {})
        self._conn.execute(
            """
            INSERT OR IGNORE INTO edges (from_name, to_name, edge_type, metadata_json)
            VALUES (?, ?, ?, ?)
            """,
            (from_name, to_name, edge_type, meta_json),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Traversal helpers
    # ------------------------------------------------------------------

    def get_callers(self, qualified_name: str) -> list[dict[str, Any]]:
        """Return nodes that *call* the given function/method.

        Follows incoming ``CALLS`` edges.
        """
        cur = self._conn.execute(
            """
            SELECT n.* FROM nodes n
            JOIN edges e ON e.from_name = n.qualified_name
            WHERE e.to_name = ? AND e.edge_type = 'CALLS'
            """,
            (qualified_name,),
        )
        return self._rows_to_list(cur)

    def get_callees(self, qualified_name: str) -> list[dict[str, Any]]:
        """Return nodes that the given function/method *calls*.

        Follows outgoing ``CALLS`` edges.
        """
        cur = self._conn.execute(
            """
            SELECT n.* FROM nodes n
            JOIN edges e ON e.to_name = n.qualified_name
            WHERE e.from_name = ? AND e.edge_type = 'CALLS'
            """,
            (qualified_name,),
        )
        return self._rows_to_list(cur)

    def get_community(self, community_id: int) -> list[dict[str, Any]]:
        """Return all nodes assigned to the given community cluster."""
        cur = self._conn.execute(
            "SELECT * FROM nodes WHERE community_id = ?", (community_id,)
        )
        return self._rows_to_list(cur)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_nodes(
        self,
        query: str,
        kind: Optional[str] = None,
        classification: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over node name, docstring, and business_summary.

        Parameters
        ----------
        query : str
            FTS5 match expression (supports ``AND``, ``OR``, ``NOT``, prefix ``*``).
        kind : str | None
            Optional filter on ``nodes.kind``.
        classification : str | None
            Optional filter on ``nodes.classification``.

        Returns
        -------
        list[dict]
            Matching node dicts ordered by FTS rank.
        """
        # Build the query dynamically to add optional filters.
        sql = """
            SELECT n.* FROM nodes n
            JOIN nodes_fts f ON n.rowid = f.rowid
            WHERE nodes_fts MATCH ?
        """
        params: list[Any] = [query]

        if kind is not None:
            sql += " AND n.kind = ?"
            params.append(kind)
        if classification is not None:
            sql += " AND n.classification = ?"
            params.append(classification)

        sql += " ORDER BY f.rank"

        cur = self._conn.execute(sql, params)
        return self._rows_to_list(cur)

    def get_business_rules(self, domain: Optional[str] = None) -> list[dict[str, Any]]:
        """Return nodes classified as ``BUSINESS_RULE``.

        Parameters
        ----------
        domain : str | None
            If provided, further filter by domain label.
        """
        if domain is not None:
            cur = self._conn.execute(
                "SELECT * FROM nodes WHERE classification = 'BUSINESS_RULE' AND domain = ?",
                (domain,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM nodes WHERE classification = 'BUSINESS_RULE'"
            )
        return self._rows_to_list(cur)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate counts by node kind and edge type.

        Returns
        -------
        dict
            ``{"total_nodes": int, "total_edges": int,
               "nodes_by_kind": {…}, "edges_by_type": {…},
               "nodes_by_classification": {…}, "nodes_by_domain": {…}}``
        """
        total_nodes = self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        total_edges = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        nodes_by_kind: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT kind, COUNT(*) AS cnt FROM nodes GROUP BY kind"
        ):
            nodes_by_kind[row["kind"]] = row["cnt"]

        edges_by_type: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT edge_type, COUNT(*) AS cnt FROM edges GROUP BY edge_type"
        ):
            edges_by_type[row["edge_type"]] = row["cnt"]

        nodes_by_classification: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT classification, COUNT(*) AS cnt FROM nodes "
            "WHERE classification IS NOT NULL GROUP BY classification"
        ):
            nodes_by_classification[row["classification"]] = row["cnt"]

        nodes_by_domain: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT domain, COUNT(*) AS cnt FROM nodes "
            "WHERE domain IS NOT NULL GROUP BY domain"
        ):
            nodes_by_domain[row["domain"]] = row["cnt"]

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_kind": nodes_by_kind,
            "edges_by_type": edges_by_type,
            "nodes_by_classification": nodes_by_classification,
            "nodes_by_domain": nodes_by_domain,
        }

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def trace_flow(
        self, entry_point: str, max_depth: int = 5
    ) -> dict[str, Any]:
        """Forward BFS from *entry_point* following outgoing edges.

        Returns
        -------
        dict
            ``{"root": str, "max_depth": int, "depth": int,
               "nodes": [dict], "edges": [dict]}``
            where *depth* is the actual deepest level reached.
        """
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(entry_point, 0)])
        result_nodes: list[dict[str, Any]] = []
        result_edges: list[dict[str, Any]] = []
        actual_depth = 0

        while queue:
            current, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            actual_depth = max(actual_depth, depth)

            node = self.get_node(current)
            if node:
                node["_depth"] = depth
                result_nodes.append(node)

            # Follow all outgoing edges
            for row in self._conn.execute(
                "SELECT * FROM edges WHERE from_name = ?", (current,)
            ):
                edge = self._row_to_dict(row)
                if edge is not None:
                    result_edges.append(edge)
                    if row["to_name"] not in visited:
                        queue.append((row["to_name"], depth + 1))

        return {
            "root": entry_point,
            "max_depth": max_depth,
            "depth": actual_depth,
            "nodes": result_nodes,
            "edges": result_edges,
        }

    def impact_analysis(
        self, qualified_name: str, max_depth: int = 3
    ) -> dict[str, Any]:
        """Reverse BFS — find all nodes that would be *affected* by a change.

        Traverses incoming edges to discover transitive dependants.

        Returns
        -------
        dict
            ``{"target": str, "max_depth": int, "depth": int,
               "affected_nodes": [dict], "affected_edges": [dict]}``
        """
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(qualified_name, 0)])
        result_nodes: list[dict[str, Any]] = []
        result_edges: list[dict[str, Any]] = []
        actual_depth = 0

        while queue:
            current, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            actual_depth = max(actual_depth, depth)

            node = self.get_node(current)
            if node:
                node["_depth"] = depth
                result_nodes.append(node)

            # Follow all *incoming* edges (reverse direction)
            for row in self._conn.execute(
                "SELECT * FROM edges WHERE to_name = ?", (current,)
            ):
                edge = self._row_to_dict(row)
                if edge is not None:
                    result_edges.append(edge)
                    if row["from_name"] not in visited:
                        queue.append((row["from_name"], depth + 1))

        return {
            "target": qualified_name,
            "max_depth": max_depth,
            "depth": actual_depth,
            "affected_nodes": result_nodes,
            "affected_edges": result_edges,
        }

    # ------------------------------------------------------------------
    # Classification update
    # ------------------------------------------------------------------

    def update_classification(
        self,
        qualified_name: str,
        classification: str,
        domain: str,
        business_summary: str,
        confidence: float,
    ) -> None:
        """Update the business classification fields for a node.

        Parameters
        ----------
        classification : str
            Must be one of :pydata:`CLASSIFICATIONS`.
        confidence : float
            Value in ``[0.0, 1.0]``.

        Raises
        ------
        ValueError
            If *classification* or *confidence* are invalid.
        KeyError
            If the node does not exist.
        """
        if classification not in CLASSIFICATIONS:
            raise ValueError(
                f"Invalid classification {classification!r}. "
                f"Must be one of {CLASSIFICATIONS}"
            )
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Confidence must be in [0.0, 1.0], got {confidence}")

        cur = self._conn.execute(
            """
            UPDATE nodes
            SET classification   = ?,
                domain           = ?,
                business_summary = ?,
                confidence       = ?
            WHERE qualified_name = ?
            """,
            (classification, domain, business_summary, confidence, qualified_name),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Node {qualified_name!r} not found")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------

    def all_nodes(self) -> list[dict[str, Any]]:
        """Return every node in the graph."""
        cur = self._conn.execute("SELECT * FROM nodes")
        return self._rows_to_list(cur)

    def all_edges(self) -> list[dict[str, Any]]:
        """Return every edge in the graph."""
        cur = self._conn.execute("SELECT * FROM edges")
        return self._rows_to_list(cur)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self) -> dict[str, Any]:
        """Export the full graph as a JSON-serialisable dictionary.

        Returns
        -------
        dict
            ``{"nodes": [dict, …], "edges": [dict, …], "stats": dict}``
        """
        return {
            "nodes": self.all_nodes(),
            "edges": self.all_edges(),
            "stats": self.get_stats(),
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> "CodeGraph":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        stats = self.get_stats()
        return (
            f"<CodeGraph db={self.db_path!r} "
            f"nodes={stats['total_nodes']} edges={stats['total_edges']}>"
        )
