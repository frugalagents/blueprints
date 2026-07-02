"""
Codebase Business Logic Extractor — MCP Server
================================================
Exposes the extracted knowledge graph via the Model Context Protocol (MCP),
allowing AI assistants (Claude Desktop, Cursor, etc.) to query business
logic, call graphs, impact analysis, and more.

Usage:
    python -m mcp_server.server          # standalone
    # Or via CLI:  python main.py serve
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

import yaml
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Initialise FastMCP application
# ---------------------------------------------------------------------------
app = FastMCP(
    "CodebaseBusinessExtractor",
    description=(
        "Query the business-logic knowledge graph extracted from a codebase. "
        "Search functions, trace business flows, analyse impact, and ask "
        "natural-language questions about how the code implements business rules."
    ),
)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
_CONFIG: dict | None = None
_DB_PATH: str | None = None


def _load_config() -> dict:
    """Load config.yaml from the project root (or use defaults)."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    config_candidates = [
        os.environ.get("CBE_CONFIG", ""),
        "config.yaml",
        str(Path(__file__).resolve().parent.parent / "config.yaml"),
    ]
    for candidate in config_candidates:
        if candidate and Path(candidate).is_file():
            with open(candidate, "r") as fh:
                _CONFIG = yaml.safe_load(fh)
                return _CONFIG

    # Sensible defaults
    _CONFIG = {
        "llm": {
            "model_id": "anthropic.claude-sonnet-4-20250514",
            "region": "us-east-1",
            "max_tokens": 4096,
            "temperature": 0.0,
        },
        "graph": {"db_path": "output/code_graph.db"},
    }
    return _CONFIG


def _get_db_path() -> str:
    """Resolve the SQLite database path."""
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    env_path = os.environ.get("CBE_DB_PATH")
    if env_path:
        _DB_PATH = env_path
    else:
        cfg = _load_config()
        _DB_PATH = cfg.get("graph", {}).get("db_path", "output/code_graph.db")
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    """Return a read-only connection to the knowledge-graph database."""
    db = _get_db_path()
    if not Path(db).is_file():
        raise FileNotFoundError(
            f"Knowledge graph database not found at '{db}'. "
            "Run 'python main.py extract <repo>' first."
        )
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Internal query helpers
# ---------------------------------------------------------------------------

def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    """Convert sqlite3.Row objects to plain dicts."""
    return [dict(r) for r in rows]


def _get_function_id(conn: sqlite3.Connection, name: str) -> int | None:
    """Look up a function node by name (exact or LIKE)."""
    row = conn.execute(
        "SELECT id FROM functions WHERE name = ? LIMIT 1", (name,)
    ).fetchone()
    if row:
        return row["id"]
    # Fallback: partial match
    row = conn.execute(
        "SELECT id FROM functions WHERE name LIKE ? LIMIT 1", (f"%{name}%",)
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@app.tool()
def search_functions(
    query: str,
    domain: str | None = None,
    kind: str | None = None,
    classification: str | None = None,
) -> list[dict]:
    """Search the knowledge graph for functions matching a query.

    Args:
        query: Free-text search against function names, docstrings, and file paths.
        domain: Filter by business domain (e.g. "billing", "auth", "orders").
        kind: Filter by kind — "function", "method", "class", "endpoint".
        classification: Filter by classification — "business", "technical", "glue", "unknown".

    Returns:
        A list of matching function records with metadata.
    """
    conn = _connect()
    try:
        clauses: list[str] = []
        params: list[str] = []

        # Full-text search across multiple columns
        clauses.append(
            "(name LIKE ? OR docstring LIKE ? OR file_path LIKE ?)"
        )
        like = f"%{query}%"
        params.extend([like, like, like])

        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if classification:
            clauses.append("classification = ?")
            params.append(classification)

        where = " AND ".join(clauses)
        sql = f"SELECT * FROM functions WHERE {where} ORDER BY name LIMIT 50"
        rows = conn.execute(sql, params).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


@app.tool()
def get_callers(function_name: str) -> list[dict]:
    """Get all functions that call the specified function (upstream callers).

    Args:
        function_name: The name of the target function.

    Returns:
        A list of caller function records.
    """
    conn = _connect()
    try:
        fid = _get_function_id(conn, function_name)
        if fid is None:
            return [{"error": f"Function '{function_name}' not found in the graph."}]

        rows = conn.execute(
            """
            SELECT f.*
            FROM calls c
            JOIN functions f ON f.id = c.caller_id
            WHERE c.callee_id = ?
            ORDER BY f.name
            """,
            (fid,),
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


@app.tool()
def get_callees(function_name: str) -> list[dict]:
    """Get all functions that the specified function calls (downstream callees).

    Args:
        function_name: The name of the source function.

    Returns:
        A list of callee function records.
    """
    conn = _connect()
    try:
        fid = _get_function_id(conn, function_name)
        if fid is None:
            return [{"error": f"Function '{function_name}' not found in the graph."}]

        rows = conn.execute(
            """
            SELECT f.*
            FROM calls c
            JOIN functions f ON f.id = c.callee_id
            WHERE c.caller_id = ?
            ORDER BY f.name
            """,
            (fid,),
        ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


@app.tool()
def get_business_rules(domain: str | None = None) -> list[dict]:
    """Retrieve extracted BDD-style business rules (Given/When/Then).

    Args:
        domain: Optional domain filter (e.g. "billing", "auth").

    Returns:
        A list of business rule records.
    """
    conn = _connect()
    try:
        if domain:
            rows = conn.execute(
                "SELECT * FROM business_rules WHERE domain = ? ORDER BY id",
                (domain,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM business_rules ORDER BY domain, id"
            ).fetchall()
        return _rows_to_dicts(rows)
    finally:
        conn.close()


@app.tool()
def trace_business_flow(entry_point: str) -> dict:
    """Trace a business flow starting from an entry point through its call chain.

    Walks the call graph from the given entry point, collecting every function
    in the transitive closure with depth annotations.  Business-classified
    nodes are highlighted.

    Args:
        entry_point: The function name to start tracing from (e.g. an API handler).

    Returns:
        A dict with the entry point, total depth, and an ordered list of steps.
    """
    conn = _connect()
    try:
        fid = _get_function_id(conn, entry_point)
        if fid is None:
            return {"error": f"Entry point '{entry_point}' not found in the graph."}

        visited: dict[int, int] = {}  # id → depth
        queue: list[tuple[int, int]] = [(fid, 0)]
        steps: list[dict] = []

        while queue:
            current_id, depth = queue.pop(0)
            if current_id in visited:
                continue
            visited[current_id] = depth

            row = conn.execute(
                "SELECT * FROM functions WHERE id = ?", (current_id,)
            ).fetchone()
            if row:
                step = dict(row)
                step["depth"] = depth
                steps.append(step)

            callees = conn.execute(
                "SELECT callee_id FROM calls WHERE caller_id = ?", (current_id,)
            ).fetchall()
            for c in callees:
                if c["callee_id"] not in visited:
                    queue.append((c["callee_id"], depth + 1))

        return {
            "entry_point": entry_point,
            "total_steps": len(steps),
            "max_depth": max((s["depth"] for s in steps), default=0),
            "steps": steps,
        }
    finally:
        conn.close()


@app.tool()
def impact_analysis(function_name: str) -> dict:
    """Analyse the blast radius if a function changes.

    Walks *upstream* through the call graph (callers of callers) to find
    every function and business rule that could be affected.

    Args:
        function_name: The function that might change.

    Returns:
        A dict with affected functions, affected business rules, and risk summary.
    """
    conn = _connect()
    try:
        fid = _get_function_id(conn, function_name)
        if fid is None:
            return {"error": f"Function '{function_name}' not found in the graph."}

        # BFS upstream (callers)
        visited: set[int] = set()
        queue: list[int] = [fid]
        affected: list[dict] = []

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            row = conn.execute(
                "SELECT * FROM functions WHERE id = ?", (current_id,)
            ).fetchone()
            if row:
                affected.append(dict(row))

            callers = conn.execute(
                "SELECT caller_id FROM calls WHERE callee_id = ?", (current_id,)
            ).fetchall()
            for c in callers:
                if c["caller_id"] not in visited:
                    queue.append(c["caller_id"])

        # Find affected business rules
        affected_ids = list(visited)
        placeholders = ",".join("?" * len(affected_ids))
        rules = []
        if affected_ids:
            rule_rows = conn.execute(
                f"""
                SELECT DISTINCT br.*
                FROM business_rules br
                JOIN rule_functions rf ON rf.rule_id = br.id
                WHERE rf.function_id IN ({placeholders})
                ORDER BY br.domain
                """,
                affected_ids,
            ).fetchall()
            rules = _rows_to_dicts(rule_rows)

        # Classify risk
        business_count = sum(
            1 for a in affected if a.get("classification") == "business"
        )
        risk = "low"
        if business_count > 5 or len(affected) > 20:
            risk = "high"
        elif business_count > 2 or len(affected) > 10:
            risk = "medium"

        return {
            "function": function_name,
            "total_affected_functions": len(affected),
            "business_functions_affected": business_count,
            "affected_business_rules": len(rules),
            "risk_level": risk,
            "affected_functions": affected,
            "affected_rules": rules,
        }
    finally:
        conn.close()


@app.tool()
def get_domain_summary(domain: str | None = None) -> dict:
    """Get an overview of one or all business domains in the codebase.

    Args:
        domain: A specific domain name, or None for all domains.

    Returns:
        A dict with domain statistics, top functions, and rule counts.
    """
    conn = _connect()
    try:
        if domain:
            funcs = conn.execute(
                "SELECT * FROM functions WHERE domain = ? ORDER BY name",
                (domain,),
            ).fetchall()
            rules = conn.execute(
                "SELECT * FROM business_rules WHERE domain = ? ORDER BY id",
                (domain,),
            ).fetchall()
            business = [f for f in funcs if dict(f).get("classification") == "business"]
            return {
                "domain": domain,
                "total_functions": len(funcs),
                "business_functions": len(business),
                "business_rules": len(rules),
                "functions": _rows_to_dicts(funcs)[:25],
                "rules": _rows_to_dicts(rules),
            }

        # All domains summary
        domain_rows = conn.execute(
            """
            SELECT domain,
                   COUNT(*) AS total_functions,
                   SUM(CASE WHEN classification = 'business' THEN 1 ELSE 0 END) AS business_functions
            FROM functions
            WHERE domain IS NOT NULL AND domain != ''
            GROUP BY domain
            ORDER BY business_functions DESC
            """
        ).fetchall()

        rule_counts = {}
        for r in conn.execute(
            "SELECT domain, COUNT(*) AS cnt FROM business_rules GROUP BY domain"
        ).fetchall():
            rule_counts[r["domain"]] = r["cnt"]

        domains = []
        for dr in domain_rows:
            d = dict(dr)
            d["business_rules"] = rule_counts.get(d["domain"], 0)
            domains.append(d)

        return {
            "total_domains": len(domains),
            "domains": domains,
        }
    finally:
        conn.close()


@app.tool()
def ask_about_codebase(question: str) -> str:
    """Ask a natural-language question about the codebase and get an AI-generated answer.

    This tool gathers relevant context from the knowledge graph and sends it
    to Amazon Bedrock (Claude) to produce a grounded answer.

    Args:
        question: Your question, e.g. "How does the billing module calculate discounts?"

    Returns:
        An AI-generated answer grounded in the extracted knowledge graph.
    """
    import boto3

    cfg = _load_config()
    llm_cfg = cfg.get("llm", {})
    model_id = llm_cfg.get("model_id", "anthropic.claude-sonnet-4-20250514")
    region = llm_cfg.get("region", "us-east-1")
    max_tokens = llm_cfg.get("max_tokens", 4096)
    temperature = llm_cfg.get("temperature", 0.0)

    # Gather context by searching the graph
    conn = _connect()
    try:
        # Search functions related to the question
        like = f"%{question.split()[0]}%"  # naive keyword
        keywords = [w for w in question.split() if len(w) > 3]

        context_functions: list[dict] = []
        for kw in keywords[:5]:
            rows = conn.execute(
                """
                SELECT name, file_path, domain, classification, docstring, summary
                FROM functions
                WHERE name LIKE ? OR docstring LIKE ? OR summary LIKE ?
                LIMIT 10
                """,
                (f"%{kw}%", f"%{kw}%", f"%{kw}%"),
            ).fetchall()
            context_functions.extend(_rows_to_dicts(rows))

        # Deduplicate by name
        seen = set()
        unique_funcs = []
        for f in context_functions:
            if f["name"] not in seen:
                seen.add(f["name"])
                unique_funcs.append(f)
        context_functions = unique_funcs[:20]

        # Get relevant business rules
        context_rules: list[dict] = []
        for kw in keywords[:3]:
            rows = conn.execute(
                """
                SELECT title, domain, given_clause, when_clause, then_clause
                FROM business_rules
                WHERE title LIKE ? OR given_clause LIKE ? OR when_clause LIKE ? OR then_clause LIKE ?
                LIMIT 5
                """,
                (f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"),
            ).fetchall()
            context_rules.extend(_rows_to_dicts(rows))
    finally:
        conn.close()

    # Build prompt
    context_text = "## Relevant Functions\n"
    for f in context_functions:
        context_text += (
            f"- **{f['name']}** ({f.get('file_path', '?')}) "
            f"[{f.get('classification', '?')}/{f.get('domain', '?')}]: "
            f"{f.get('summary') or f.get('docstring') or 'No description'}\n"
        )

    context_text += "\n## Relevant Business Rules\n"
    for r in context_rules:
        context_text += (
            f"- **{r.get('title', 'Rule')}** [{r.get('domain', '?')}]\n"
            f"  Given: {r.get('given_clause', '')}\n"
            f"  When: {r.get('when_clause', '')}\n"
            f"  Then: {r.get('then_clause', '')}\n"
        )

    system_prompt = (
        "You are an expert software analyst. Answer questions about a codebase "
        "using ONLY the context provided below. If the context is insufficient, "
        "say so. Be specific and reference function names and domains.\n\n"
        f"{context_text}"
    )

    # Call Bedrock
    client = boto3.client("bedrock-runtime", region_name=region)
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": question}],
            "system": system_prompt,
        }
    )
    response = client.invoke_model(modelId=model_id, body=body)
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run()
