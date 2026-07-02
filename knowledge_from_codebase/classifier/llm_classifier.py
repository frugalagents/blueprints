"""
classifier.llm_classifier — Pipeline-facing wrapper for LLM classification.

Provides :func:`classify_functions` with the signature that ``main.py``
expects.  Reads functions from the SQLite database, runs them through
:class:`~classifier.business_classifier.BusinessClassifier`, and writes
the results back.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List

from .business_classifier import BusinessClassifier

logger = logging.getLogger(__name__)


def classify_functions(
    *,
    db_path: str,
    model_id: str = "anthropic.claude-sonnet-4-20250514",
    region: str = "us-east-1",
    batch_size: int = 5,
    skip_technical: bool = False,
    verbose: bool = False,
) -> None:
    """Classify every function in the database via LLM.

    Reads from the ``functions`` table, calls the Bedrock-powered
    :class:`BusinessClassifier`, and writes ``classification``, ``domain``,
    ``summary``, and ``confidence`` back to both ``functions`` and ``nodes``.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database.
    model_id : str
        Bedrock model identifier.
    region : str
        AWS region.
    batch_size : int
        Functions per batched LLM call.
    skip_technical : bool
        If ``True``, skip functions whose names strongly suggest infrastructure
        (e.g. ``__init__``, ``__repr__``, ``setup_logging``).
    verbose : bool
        Log progress details.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Load all functions
    rows = conn.execute(
        "SELECT id, name, qualified_name, kind, file_path, docstring, source_code "
        "FROM functions"
    ).fetchall()

    if not rows:
        logger.info("No functions to classify")
        conn.close()
        return

    logger.info("Classifying %d functions (batch_size=%d)", len(rows), batch_size)

    # Build function dicts for the classifier
    func_dicts: List[Dict[str, Any]] = []
    for row in rows:
        name = row["name"]

        # Optionally skip obviously technical functions
        if skip_technical and _is_obviously_technical(name):
            _write_classification(
                conn,
                row["id"],
                row["qualified_name"],
                classification="technical",
                domain="Infrastructure",
                summary="Technical infrastructure function (auto-skipped).",
                confidence=0.9,
            )
            continue

        func_dicts.append({
            "function_name": name,
            "file_path": row["file_path"],
            "kind": row["kind"],
            "language": "python",
            "docstring": row["docstring"] or "",
            "source_code": row["source_code"] or "",
            "_id": row["id"],
            "_qualified_name": row["qualified_name"],
        })

    if not func_dicts:
        logger.info("All functions were auto-skipped")
        conn.close()
        return

    # Create classifier
    classifier = BusinessClassifier(model_id=model_id, region=region)

    # Build a lightweight graph adapter for neighbourhood lookups
    graph_adapter = _GraphAdapter(conn)

    # Classify in batches
    results = classifier.classify_batch(
        func_dicts,
        graph=graph_adapter,
        batch_size=batch_size,
    )

    # Write results back
    for func, result in zip(func_dicts, results):
        classification = _map_classification(result.get("classification", ""))
        domain = result.get("business_domain", "")
        summary = result.get("business_summary", "")
        confidence = result.get("confidence", 0.0)

        _write_classification(
            conn,
            func["_id"],
            func["_qualified_name"],
            classification=classification,
            domain=domain,
            summary=summary,
            confidence=confidence,
        )

        if verbose:
            logger.info(
                "  %s → %s (%.0f%%) [%s]",
                func["function_name"],
                classification,
                confidence * 100,
                domain,
            )

    conn.commit()
    conn.close()
    logger.info("Classification complete")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TECHNICAL_PATTERNS = {
    "__init__", "__repr__", "__str__", "__eq__", "__hash__",
    "__enter__", "__exit__", "__del__", "__len__", "__iter__",
    "__next__", "__getattr__", "__setattr__", "__getitem__",
    "setup_logging", "configure_logging", "main",
}


def _is_obviously_technical(name: str) -> bool:
    """Heuristic: skip dunder methods and common infra function names."""
    if name in _TECHNICAL_PATTERNS:
        return True
    if name.startswith("__") and name.endswith("__"):
        return True
    return False


def _map_classification(raw: str) -> str:
    """Map the 5-category LLM classification to the 3-category scheme
    used by the dashboard (business / technical / glue)."""
    raw_upper = raw.upper().replace(" ", "_").replace("-", "_")
    if raw_upper in ("BUSINESS_RULE", "BUSINESS_PROCESS"):
        return "business"
    if raw_upper in ("TECHNICAL_INFRASTRUCTURE", "DATA_ACCESS"):
        return "technical"
    if raw_upper == "INTEGRATION":
        return "glue"
    return "technical"


def _write_classification(
    conn: sqlite3.Connection,
    func_id: int,
    qualified_name: str,
    *,
    classification: str,
    domain: str,
    summary: str,
    confidence: float,
) -> None:
    """Write classification to both ``functions`` and ``nodes`` tables."""
    conn.execute(
        """
        UPDATE functions
        SET classification = ?, domain = ?, summary = ?, confidence = ?
        WHERE id = ?
        """,
        (classification, domain, summary, confidence, func_id),
    )
    conn.execute(
        """
        UPDATE nodes
        SET classification = ?, domain = ?, business_summary = ?, confidence = ?
        WHERE qualified_name = ?
        """,
        (classification, domain, summary, confidence, qualified_name),
    )


class _GraphAdapter:
    """Lightweight adapter that exposes ``get_callers`` / ``get_callees``
    from the ``calls`` + ``functions`` tables for the classifier."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_callers(self, name: str) -> List[str]:
        rows = self._conn.execute(
            """
            SELECT f2.name FROM calls c
            JOIN functions f1 ON f1.id = c.callee_id
            JOIN functions f2 ON f2.id = c.caller_id
            WHERE f1.name = ?
            LIMIT 10
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
            WHERE f1.name = ?
            LIMIT 10
            """,
            (name,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_community(self, name: str) -> str:
        row = self._conn.execute(
            "SELECT community_id FROM functions WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return str(row[0]) if row and row[0] is not None else "N/A"
