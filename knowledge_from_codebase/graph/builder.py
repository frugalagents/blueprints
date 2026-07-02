"""
graph.builder — Populate the CodeGraph from parsed repository data.

Provides :func:`build_graph`, the single entry point that ``main.py``
calls for Stage 2.  It takes the output of :func:`parser.code_parser.parse_repository`,
creates/opens a :class:`~graph.store.CodeGraph`, inserts all nodes and edges,
and creates the ``functions`` / ``calls`` / ``business_rules`` compatibility
views used by ``main.py`` and the MCP server.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict

from .store import CodeGraph

logger = logging.getLogger(__name__)


def build_graph(
    parsed: Dict[str, Any],
    *,
    db_path: str = "output/code_graph.db",
    verbose: bool = False,
) -> CodeGraph:
    """Build (or rebuild) the code graph from parsed repository data.

    Parameters
    ----------
    parsed : dict
        Output of :func:`parser.code_parser.parse_repository`.  Must contain
        ``file_structures``, ``call_graph``, and ``dependencies``.
    db_path : str
        Path to the SQLite database file.
    verbose : bool
        Log progress details.

    Returns
    -------
    CodeGraph
        The populated graph (also persisted to *db_path*).
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    graph = CodeGraph(db_path)

    file_structures = parsed.get("file_structures", {})
    call_graph = parsed.get("call_graph", {})
    dependencies = parsed.get("dependencies", [])

    # ------------------------------------------------------------------
    # 1. Insert nodes from file structures
    # ------------------------------------------------------------------
    node_count = 0

    for rel_path, struct in file_structures.items():
        filepath = struct.get("filepath", rel_path)

        # Top-level functions
        for fn in struct.get("functions", []):
            qname = f"{rel_path}::{fn['name']}"
            graph.add_node(
                qualified_name=qname,
                name=fn["name"],
                kind="Function",
                file=rel_path,
                start_line=fn.get("start_line", 0),
                end_line=fn.get("end_line", 0),
                docstring=fn.get("docstring") or "",
                source_snippet=fn.get("source", ""),
                metadata={
                    "args": fn.get("args", []),
                    "return_type": fn.get("return_type"),
                    "decorators": fn.get("decorators", []),
                    "is_async": fn.get("is_async", False),
                },
            )
            node_count += 1

        # Classes and their methods
        for cls in struct.get("classes", []):
            cls_qname = f"{rel_path}::{cls['name']}"
            graph.add_node(
                qualified_name=cls_qname,
                name=cls["name"],
                kind="Class",
                file=rel_path,
                start_line=cls.get("start_line", 0),
                end_line=cls.get("end_line", 0),
                docstring=cls.get("docstring") or "",
                source_snippet="",
                metadata={
                    "bases": cls.get("bases", []),
                    "decorators": cls.get("decorators", []),
                },
            )
            node_count += 1

            for method in cls.get("methods", []):
                m_qname = f"{rel_path}::{cls['name']}.{method['name']}"
                graph.add_node(
                    qualified_name=m_qname,
                    name=f"{cls['name']}.{method['name']}",
                    kind="Method",
                    file=rel_path,
                    start_line=method.get("start_line", 0),
                    end_line=method.get("end_line", 0),
                    docstring=method.get("docstring") or "",
                    source_snippet=method.get("source", ""),
                    metadata={
                        "args": method.get("args", []),
                        "return_type": method.get("return_type"),
                        "decorators": method.get("decorators", []),
                        "is_async": method.get("is_async", False),
                        "class": cls["name"],
                    },
                )
                node_count += 1

                # CONTAINS edge: class → method
                graph.add_edge(cls_qname, m_qname, "CONTAINS")

    if verbose:
        logger.info("Inserted %d nodes", node_count)

    # ------------------------------------------------------------------
    # 2. Insert call edges
    # ------------------------------------------------------------------
    edge_count = 0
    for edge in call_graph.get("edges", []):
        if edge.get("resolved"):
            graph.add_edge(
                edge["source"],
                edge["target"],
                "CALLS",
                metadata={
                    "call_name": edge.get("call_name", ""),
                    "line": edge.get("line", 0),
                },
            )
            edge_count += 1

    if verbose:
        logger.info("Inserted %d call edges", edge_count)

    # ------------------------------------------------------------------
    # 3. Insert dependency (IMPORTS) edges
    # ------------------------------------------------------------------
    dep_count = 0
    for dep in dependencies:
        if dep.get("resolved") and dep.get("category") == "internal":
            source_file = dep.get("source_file", "")
            target_file = dep.get("target_file", "")
            if source_file and target_file:
                # Create module-level pseudo-nodes if needed
                for f in (source_file, target_file):
                    mod_qname = f"{f}::<module>"
                    existing = graph.get_node(mod_qname)
                    if not existing:
                        graph.add_node(
                            qualified_name=mod_qname,
                            name=f"<module:{Path(f).stem}>",
                            kind="Module",
                            file=f,
                        )
                graph.add_edge(
                    f"{source_file}::<module>",
                    f"{target_file}::<module>",
                    "IMPORTS",
                    metadata={
                        "import_name": dep.get("import_name", ""),
                        "line": dep.get("line", 0),
                    },
                )
                dep_count += 1

    if verbose:
        logger.info("Inserted %d import edges", dep_count)

    # ------------------------------------------------------------------
    # 4. Create compatibility views for main.py / MCP server
    # ------------------------------------------------------------------
    _create_compat_tables(graph)

    graph.close()

    total_stats = graph_stats_from_db(db_path)
    if verbose:
        logger.info(
            "Graph built: %d nodes, %d edges → %s",
            total_stats["total_nodes"],
            total_stats["total_edges"],
            db_path,
        )

    return graph


def _create_compat_tables(graph: CodeGraph) -> None:
    """Create ``functions``, ``calls``, and ``business_rules`` tables.

    ``main.py``, the ``stats`` command, and the MCP server all query these
    table names directly.  We materialise them from the canonical ``nodes``
    and ``edges`` tables so both APIs work.
    """
    conn = graph._conn

    conn.executescript(
        """
        -- Drop old compat tables so we can rebuild
        DROP TABLE IF EXISTS functions;
        DROP TABLE IF EXISTS calls;

        -- functions: one row per Function / Method / Endpoint node
        CREATE TABLE functions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'function',
            file_path   TEXT NOT NULL DEFAULT '',
            start_line  INTEGER NOT NULL DEFAULT 0,
            end_line    INTEGER NOT NULL DEFAULT 0,
            docstring   TEXT NOT NULL DEFAULT '',
            source_code TEXT NOT NULL DEFAULT '',
            domain      TEXT,
            classification TEXT,
            summary     TEXT,
            confidence  REAL,
            community_id INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        INSERT INTO functions (
            name, qualified_name, kind, file_path, start_line, end_line,
            docstring, source_code, domain, classification, summary,
            confidence, community_id, metadata_json
        )
        SELECT
            name, qualified_name, kind, file, start_line, end_line,
            docstring, source_snippet, domain, classification,
            business_summary, confidence, community_id, metadata_json
        FROM nodes
        WHERE kind IN ('Function', 'Method', 'Endpoint');

        -- calls: one row per CALLS edge, referencing functions.id
        CREATE TABLE calls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id   INTEGER NOT NULL,
            callee_id   INTEGER NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        INSERT INTO calls (caller_id, callee_id, metadata_json)
        SELECT
            f1.id,
            f2.id,
            e.metadata_json
        FROM edges e
        JOIN functions f1 ON f1.qualified_name = e.from_name
        JOIN functions f2 ON f2.qualified_name = e.to_name
        WHERE e.edge_type = 'CALLS';

        -- business_rules: starts empty, populated by the extractor stage
        CREATE TABLE IF NOT EXISTS business_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL DEFAULT '',
            domain          TEXT,
            given_clause    TEXT NOT NULL DEFAULT '',
            when_clause     TEXT NOT NULL DEFAULT '',
            then_clause     TEXT NOT NULL DEFAULT '',
            source_function TEXT,
            source_lines    TEXT,
            source_snippet  TEXT NOT NULL DEFAULT '',
            confidence      REAL,
            business_impact TEXT NOT NULL DEFAULT ''
        );

        -- rule_functions: links rules to the functions they were extracted from
        CREATE TABLE IF NOT EXISTS rule_functions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id     INTEGER NOT NULL,
            function_id INTEGER NOT NULL,
            UNIQUE(rule_id, function_id)
        );

        -- flows: starts empty, populated by the flow mapper stage
        CREATE TABLE IF NOT EXISTS flows (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL DEFAULT '',
            domain      TEXT,
            description TEXT NOT NULL DEFAULT '',
            entry_point TEXT,
            steps_json  TEXT NOT NULL DEFAULT '[]'
        );

        -- Indexes for the compat tables
        CREATE INDEX IF NOT EXISTS idx_functions_name ON functions(name);
        CREATE INDEX IF NOT EXISTS idx_functions_domain ON functions(domain);
        CREATE INDEX IF NOT EXISTS idx_functions_classification ON functions(classification);
        CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id);
        CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_id);
        CREATE INDEX IF NOT EXISTS idx_rules_domain ON business_rules(domain);
        """
    )
    conn.commit()


def graph_stats_from_db(db_path: str) -> Dict[str, int]:
    """Quick stats read from the database."""
    conn = sqlite3.connect(db_path)
    try:
        total_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {"total_nodes": total_nodes, "total_edges": total_edges}
    finally:
        conn.close()
