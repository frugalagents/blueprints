"""
parser.code_parser — Pipeline-facing wrapper around the parser package.

Provides :func:`parse_repository`, the single entry point that ``main.py``
calls for Stage 1.  It delegates to :func:`parser.parse_codebase` and
:func:`parser.build_call_graph`, then merges the results into a unified
dict ready for the graph-builder stage.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import parse_codebase, build_call_graph, resolve_dependencies

logger = logging.getLogger(__name__)


def parse_repository(
    repo_path: str,
    *,
    languages: List[str] | None = None,
    exclude_patterns: List[str] | None = None,
    exclude_files: List[str] | None = None,
    min_lines: int = 3,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Parse a repository and return structures + call graph + dependencies.

    Parameters
    ----------
    repo_path : str
        Path to the repository root.
    languages : list[str] | None
        Languages to include (currently only ``"python"`` is deeply parsed).
    exclude_patterns : list[str] | None
        Directory name patterns to skip (e.g. ``["__pycache__", ".git"]``).
    exclude_files : list[str] | None
        File names to skip (e.g. ``["__init__.py", "setup.py"]``).
    min_lines : int
        Skip functions shorter than this many lines.
    verbose : bool
        If ``True``, log progress details.

    Returns
    -------
    dict
        ``{
            "repo_path": str,
            "file_structures": {rel_path: structure_dict, …},
            "call_graph": {"nodes": […], "edges": […]},
            "dependencies": [{…}, …],
            "stats": {"files_parsed": int, "total_functions": int, …},
        }``
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise NotADirectoryError(f"{repo} is not a directory")

    # Build the set of extensions from requested languages
    _LANG_EXTENSIONS = {
        "python": {".py"},
        "javascript": {".js", ".jsx", ".mjs"},
        "typescript": {".ts", ".tsx"},
        "java": {".java"},
    }
    extensions: set[str] = set()
    for lang in (languages or ["python"]):
        extensions |= _LANG_EXTENSIONS.get(lang, set())

    skip_dirs = set(exclude_patterns or [])
    skip_files = set(exclude_files or [])

    if verbose:
        logger.info("Parsing %s  extensions=%s  skip_dirs=%s", repo, extensions, skip_dirs)

    # --- 1. Parse file structures ---
    file_structures = parse_codebase(
        str(repo),
        extensions=extensions or None,
        skip_dirs=skip_dirs or None,
    )

    # Filter out excluded file names
    if skip_files:
        file_structures = {
            path: struct
            for path, struct in file_structures.items()
            if Path(path).name not in skip_files
        }

    # Filter out functions shorter than min_lines
    if min_lines > 1:
        for path, struct in file_structures.items():
            struct["functions"] = [
                fn for fn in struct.get("functions", [])
                if (fn.get("end_line", 0) - fn.get("start_line", 0) + 1) >= min_lines
            ]
            for cls in struct.get("classes", []):
                cls["methods"] = [
                    m for m in cls.get("methods", [])
                    if (m.get("end_line", 0) - m.get("start_line", 0) + 1) >= min_lines
                ]

    # --- 2. Build call graph ---
    call_graph = build_call_graph(file_structures)

    # --- 3. Resolve dependencies ---
    dependencies = resolve_dependencies(file_structures, str(repo))

    # --- 4. Compute stats ---
    total_functions = 0
    total_classes = 0
    for struct in file_structures.values():
        total_functions += len(struct.get("functions", []))
        total_classes += len(struct.get("classes", []))
        for cls in struct.get("classes", []):
            total_functions += len(cls.get("methods", []))

    stats = {
        "files_parsed": len(file_structures),
        "total_functions": total_functions,
        "total_classes": total_classes,
        "call_graph_nodes": len(call_graph.get("nodes", [])),
        "call_graph_edges": len(call_graph.get("edges", [])),
        "dependency_edges": len(dependencies),
    }

    if verbose:
        logger.info(
            "Parsed %d files: %d functions, %d classes, %d call edges",
            stats["files_parsed"],
            stats["total_functions"],
            stats["total_classes"],
            stats["call_graph_edges"],
        )

    return {
        "repo_path": str(repo),
        "file_structures": file_structures,
        "call_graph": call_graph,
        "dependencies": dependencies,
        "stats": stats,
    }
