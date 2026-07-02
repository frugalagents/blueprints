"""
extractor.flow_mapper — Pipeline-facing wrapper for business flow mapping.

Provides :func:`map_flows` with the signature that ``main.py`` expects.
Groups business-classified functions by domain, identifies end-to-end
flows via :class:`~classifier.flow_mapper.FlowMapper`, and writes the
results to the ``flows`` table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from typing import Any, Dict, List

from classifier.flow_mapper import FlowMapper as _FlowMapper

logger = logging.getLogger(__name__)


def map_flows(
    *,
    db_path: str,
    model_id: str = "anthropic.claude-sonnet-4-20250514",
    region: str = "us-east-1",
    verbose: bool = False,
) -> None:
    """Identify business flows and persist them to the database.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database.
    model_id : str
        Bedrock model identifier.
    region : str
        AWS region.
    verbose : bool
        Log progress details.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Load business-classified functions grouped by domain
    rows = conn.execute(
        """
        SELECT name AS function_name, file_path, domain,
               classification, summary AS business_summary
        FROM functions
        WHERE classification = 'business' AND domain IS NOT NULL AND domain != ''
        """
    ).fetchall()

    if not rows:
        logger.info("No business functions with domains — skipping flow mapping")
        conn.close()
        return

    # Group by domain
    domain_functions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        domain_functions[r["domain"]].append(dict(r))

    logger.info(
        "Mapping flows across %d domains (%d functions)",
        len(domain_functions),
        len(rows),
    )

    mapper = _FlowMapper(model_id=model_id, region=region)
    graph_adapter = _GraphAdapter(conn)

    try:
        flows = mapper.identify_flows(
            graph=graph_adapter,
            domain_functions=dict(domain_functions),
        )
    except Exception as exc:
        logger.warning("Flow mapping failed: %s", exc)
        flows = []

    # Clear previous flows and write new ones
    conn.execute("DELETE FROM flows")

    for flow in flows:
        # Build steps JSON with function metadata
        steps = []
        for step_desc in flow.get("steps", []):
            steps.append({"description": step_desc})

        # Try to enrich steps with function info
        for func_name in flow.get("functions_involved", []):
            row = conn.execute(
                "SELECT name, file_path, classification, summary "
                "FROM functions WHERE name = ? LIMIT 1",
                (func_name,),
            ).fetchone()
            if row:
                steps.append({
                    "function_name": row["name"],
                    "file_path": row["file_path"],
                    "classification": row["classification"],
                    "summary": row["summary"],
                })

        entry_point = ""
        if flow.get("functions_involved"):
            entry_point = flow["functions_involved"][0]

        conn.execute(
            """
            INSERT INTO flows (name, domain, description, entry_point, steps_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                flow.get("flow_name", "Unnamed Flow"),
                flow.get("domain", ""),
                flow.get("description", ""),
                entry_point,
                json.dumps(steps),
            ),
        )

    conn.commit()
    conn.close()
    logger.info("Mapped %d business flows", len(flows))


class _GraphAdapter:
    """Lightweight adapter for FlowMapper's graph neighbourhood lookups."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_callers(self, name: str) -> List[str]:
        rows = self._conn.execute(
            """
            SELECT f2.name FROM calls c
            JOIN functions f1 ON f1.id = c.callee_id
            JOIN functions f2 ON f2.id = c.caller_id
            WHERE f1.name = ? LIMIT 10
            """,
            (name,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_callees(self, name: str) -> List[str]:
        rows = self._conn.execute(
            """
            SELECT f2.name FROM calls c
            JOIN functions f1 ON f1.id = c.caller_id
            JOIN functions f2 ON f2.id = c.callee_id
            WHERE f1.name = ? LIMIT 10
            """,
            (name,),
        ).fetchall()
        return [r[0] for r in rows]
