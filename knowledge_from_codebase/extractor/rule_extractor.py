"""
extractor.rule_extractor — Pipeline-facing wrapper for BDD rule extraction.

Provides :func:`extract_rules` with the signature that ``main.py`` expects.
Reads business-classified functions from the database, runs them through
:class:`~classifier.rule_extractor.RuleExtractor`, and writes the resulting
BDD rules to the ``business_rules`` and ``rule_functions`` tables.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List

from classifier.rule_extractor import RuleExtractor as _RuleExtractor

logger = logging.getLogger(__name__)

# Only extract rules from these classifications
_ELIGIBLE = {"business"}


def extract_rules(
    *,
    db_path: str,
    model_id: str = "anthropic.claude-sonnet-4-20250514",
    region: str = "us-east-1",
    verbose: bool = False,
) -> None:
    """Extract BDD business rules and persist them to the database.

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

    # Load eligible functions
    rows = conn.execute(
        """
        SELECT id, name, qualified_name, file_path, domain,
               classification, summary, source_code
        FROM functions
        WHERE classification IN ('business')
        """
    ).fetchall()

    if not rows:
        logger.info("No business-classified functions — skipping rule extraction")
        conn.close()
        return

    logger.info("Extracting rules from %d business functions", len(rows))

    extractor = _RuleExtractor(model_id=model_id, region=region)

    # Clear previous rules
    conn.execute("DELETE FROM business_rules")
    conn.execute("DELETE FROM rule_functions")
    conn.commit()

    total_rules = 0

    for row in rows:
        func_meta = {
            "function_name": row["name"],
            "business_domain": row["domain"] or "Unknown",
            "classification": "BUSINESS_RULE",
            "business_summary": row["summary"] or "",
        }
        source_code = row["source_code"] or ""

        # Gather related functions (callers + callees)
        related = _get_related_functions(conn, row["id"])

        try:
            rules = extractor.extract_rules(
                func_metadata=func_meta,
                source_code=source_code,
                related_functions=related,
            )
        except Exception as exc:
            logger.warning("Rule extraction failed for %s: %s", row["name"], exc)
            rules = []

        for rule in rules:
            cur = conn.execute(
                """
                INSERT INTO business_rules
                    (title, domain, given_clause, when_clause, then_clause,
                     source_function, source_lines, source_snippet,
                     confidence, business_impact)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.get("rule_name", "Untitled"),
                    rule.get("business_domain", row["domain"]),
                    rule.get("given", ""),
                    rule.get("when", ""),
                    rule.get("then", ""),
                    row["name"],
                    json.dumps(rule.get("source_lines", [])),
                    rule.get("source_snippet", ""),
                    rule.get("confidence", 0.5),
                    rule.get("business_impact", ""),
                ),
            )
            rule_id = cur.lastrowid

            # Link rule to its source function
            conn.execute(
                "INSERT OR IGNORE INTO rule_functions (rule_id, function_id) VALUES (?, ?)",
                (rule_id, row["id"]),
            )
            total_rules += 1

        if verbose and rules:
            logger.info("  %s → %d rules", row["name"], len(rules))

    conn.commit()
    conn.close()
    logger.info("Extracted %d business rules total", total_rules)


def _get_related_functions(
    conn: sqlite3.Connection, func_id: int
) -> List[Dict[str, Any]]:
    """Gather callers and callees for context."""
    related: List[Dict[str, Any]] = []

    # Callers
    for r in conn.execute(
        """
        SELECT f.name AS function_name, f.classification, f.summary AS business_summary
        FROM calls c JOIN functions f ON f.id = c.caller_id
        WHERE c.callee_id = ? LIMIT 5
        """,
        (func_id,),
    ).fetchall():
        related.append(dict(r))

    # Callees
    for r in conn.execute(
        """
        SELECT f.name AS function_name, f.classification, f.summary AS business_summary
        FROM calls c JOIN functions f ON f.id = c.callee_id
        WHERE c.caller_id = ? LIMIT 5
        """,
        (func_id,),
    ).fetchall():
        related.append(dict(r))

    return related
