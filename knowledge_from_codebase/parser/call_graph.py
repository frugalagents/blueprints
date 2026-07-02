"""
call_graph — Build a cross-file call graph from extracted file structures.

The module works in two phases:

1. **Extraction** — :func:`extract_call_sites` walks the AST of a single
   source string and records every ``ast.Call`` node (function name, line
   number, enclosing scope).

2. **Resolution** — :func:`build_call_graph` takes the full set of
   per-file structures (as produced by :func:`ast_extractor.extract_file_structure`)
   and resolves each call site to a concrete ``(file, function)`` target
   when possible, producing a graph of ``{nodes, edges}``.

Only the Python standard library is used.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_call_name(node: ast.Call) -> Optional[str]:
    """Extract a dotted name string from a Call node's ``func`` attribute.

    Returns ``None`` for dynamic / computed calls that cannot be statically
    represented as a name (e.g. ``getattr(obj, name)()``).
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: List[str] = [func.attr]
        current: ast.expr = func.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
    # Subscript, starred, or other computed call — give up.
    return None


# ---------------------------------------------------------------------------
# Phase 1 — per-file call-site extraction
# ---------------------------------------------------------------------------

def extract_call_sites(source: str) -> List[Dict[str, Any]]:
    """Parse *source* and return every ``ast.Call`` as a dict.

    Parameters
    ----------
    source:
        Raw Python source code (as a string).

    Returns
    -------
    list[dict]
        Each dict contains:

        * ``name``  — dotted name of the callee, or ``None`` if dynamic.
        * ``line``  — 1-indexed line number of the call.
        * ``col``   — 0-indexed column offset.
        * ``args_count`` — number of positional arguments.
        * ``has_kwargs`` — ``True`` if the call has any ``**kwargs``.

    If the source cannot be parsed, an empty list is returned.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    sites: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _resolve_call_name(node)
        sites.append({
            "name": name,
            "line": getattr(node, "lineno", None),
            "col": getattr(node, "col_offset", None),
            "args_count": len(node.args),
            "has_kwargs": any(isinstance(a, ast.keyword) for a in node.keywords),
        })
    return sites


# ---------------------------------------------------------------------------
# Phase 1b — enclosing-function lookup
# ---------------------------------------------------------------------------

def find_enclosing_function(
    line: int,
    functions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return the function dict that encloses *line*, or ``None``.

    If multiple functions span *line* (e.g. nested defs), the **innermost**
    (smallest span) is returned.

    Parameters
    ----------
    line:
        1-indexed source line number.
    functions:
        List of function metadata dicts as returned by
        :func:`ast_extractor.extract_file_structure` (must contain at least
        ``start_line`` and ``end_line`` keys).

    Returns
    -------
    dict | None
        The matching function dict, or ``None`` if *line* is at module level.
    """
    best: Optional[Dict[str, Any]] = None
    best_span: int = float("inf")  # type: ignore[assignment]
    for fn in functions:
        start = fn.get("start_line", 0)
        end = fn.get("end_line", 0)
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best = fn
                best_span = span
    return best


# ---------------------------------------------------------------------------
# Phase 2 — cross-file call-graph resolution
# ---------------------------------------------------------------------------

def build_call_graph(file_structures: Dict[str, dict]) -> Dict[str, Any]:
    """Construct a cross-file call graph.

    Parameters
    ----------
    file_structures:
        Mapping of ``{relative_path: structure_dict}`` as returned by
        :func:`parse_codebase <parser.parse_codebase>`.

    Returns
    -------
    dict
        ``nodes`` — list of dicts, each representing a callable:

        .. code-block:: python

            {
                "id": "path/to/file.py::function_name",
                "file": "path/to/file.py",
                "name": "function_name",
                "type": "function" | "method",
                "start_line": 10,
                "end_line": 25,
            }

        ``edges`` — list of dicts, each representing a call:

        .. code-block:: python

            {
                "source": "path/a.py::caller",
                "target": "path/b.py::callee",
                "call_name": "callee",
                "line": 15,
                "resolved": True,
            }

        Unresolved calls (where the target could not be mapped to a known
        node) still appear in ``edges`` with ``resolved=False`` and a
        ``target`` of ``"<unresolved>::call_name"``.
    """

    # -- 1. Build a lookup: short_name → list of node-ids -----------------
    nodes: List[Dict[str, Any]] = []
    name_to_ids: Dict[str, List[str]] = {}  # "func_name" → ["a.py::func_name", …]

    for rel_path, structure in file_structures.items():
        # Top-level functions
        for fn in structure.get("functions", []):
            node_id = f"{rel_path}::{fn['name']}"
            nodes.append({
                "id": node_id,
                "file": rel_path,
                "name": fn["name"],
                "type": "function",
                "start_line": fn.get("start_line"),
                "end_line": fn.get("end_line"),
            })
            name_to_ids.setdefault(fn["name"], []).append(node_id)

        # Methods inside classes
        for cls in structure.get("classes", []):
            for method in cls.get("methods", []):
                qualified = f"{cls['name']}.{method['name']}"
                node_id = f"{rel_path}::{qualified}"
                nodes.append({
                    "id": node_id,
                    "file": rel_path,
                    "name": qualified,
                    "type": "method",
                    "class": cls["name"],
                    "start_line": method.get("start_line"),
                    "end_line": method.get("end_line"),
                })
                name_to_ids.setdefault(method["name"], []).append(node_id)
                name_to_ids.setdefault(qualified, []).append(node_id)

    # -- 2. Build an import alias map per file ----------------------------
    #    e.g.  {"utils.py": {"helper": "lib/helpers.py::helper"}}
    file_import_map: Dict[str, Dict[str, List[str]]] = {}

    for rel_path, structure in file_structures.items():
        alias_map: Dict[str, List[str]] = {}
        for imp in structure.get("imports", []):
            imported_name = imp.get("alias") or imp.get("name", "")
            # Try to resolve to a known node
            candidates = name_to_ids.get(imp.get("name", ""), [])
            if candidates:
                alias_map[imported_name] = candidates
        file_import_map[rel_path] = alias_map

    # -- 3. Walk call sites and build edges --------------------------------
    edges: List[Dict[str, Any]] = []

    for rel_path, structure in file_structures.items():
        filepath = structure.get("filepath", "")
        # We need the raw source to extract call sites.
        try:
            from pathlib import Path as _P
            source = _P(filepath).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        all_functions = list(structure.get("functions", []))
        for cls in structure.get("classes", []):
            for method in cls.get("methods", []):
                # Tag method with qualified name for caller resolution
                enriched = dict(method, name=f"{cls['name']}.{method['name']}")
                all_functions.append(enriched)

        call_sites = extract_call_sites(source)
        import_aliases = file_import_map.get(rel_path, {})

        for site in call_sites:
            raw_name: Optional[str] = site.get("name")
            if raw_name is None:
                continue  # dynamic call — skip

            line = site.get("line", 0)

            # Determine the caller node
            enclosing = find_enclosing_function(line, all_functions)
            if enclosing:
                source_id = f"{rel_path}::{enclosing['name']}"
            else:
                source_id = f"{rel_path}::<module>"

            # --- Resolve the target ---
            # Strategy A: the call name matches an import alias → use it
            leaf_name = raw_name.split(".")[-1]
            first_part = raw_name.split(".")[0]

            resolved = False
            target_id = f"<unresolved>::{raw_name}"

            # Check import aliases first (preferred)
            if first_part in import_aliases:
                candidates = import_aliases[first_part]
                # Pick same-file first, else first match
                target_id = candidates[0]
                resolved = True
            # Check direct name match in known nodes
            elif raw_name in name_to_ids:
                candidates = name_to_ids[raw_name]
                # Prefer same-file target
                same_file = [c for c in candidates if c.startswith(rel_path + "::")]
                target_id = same_file[0] if same_file else candidates[0]
                resolved = True
            elif leaf_name in name_to_ids:
                candidates = name_to_ids[leaf_name]
                same_file = [c for c in candidates if c.startswith(rel_path + "::")]
                target_id = same_file[0] if same_file else candidates[0]
                resolved = True

            edges.append({
                "source": source_id,
                "target": target_id,
                "call_name": raw_name,
                "line": line,
                "resolved": resolved,
            })

    return {"nodes": nodes, "edges": edges}
