"""
parser — Static analysis module for the CodebaseBusinessExtractor blueprint.

Provides three core capabilities:
  • parse_codebase   – Walk a directory tree and extract structural metadata
                       from every supported source file.
  • build_call_graph – Construct a cross-file call graph from the extracted
                       structures.
  • resolve_dependencies – Map every import statement back to the concrete
                           file that satisfies it.

All analysis uses Python's built-in ``ast`` module (no tree-sitter or other
native extensions required).  Only the Python standard library is needed.

Quick start
-----------
>>> from parser import parse_codebase, build_call_graph, resolve_dependencies
>>> structures = parse_codebase("/path/to/repo")
>>> graph       = build_call_graph(structures)
>>> deps        = resolve_dependencies(structures, "/path/to/repo")
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .ast_extractor import (
    detect_language,
    extract_file_structure,
    extract_source_snippet,
)
from .call_graph import (
    build_call_graph,
    extract_call_sites,
    find_enclosing_function,
)
from .dependency_resolver import (
    build_dependency_graph,
    resolve_module_path,
)

__all__ = [
    # High-level convenience API
    "parse_codebase",
    "build_call_graph",
    "resolve_dependencies",
    # Re-exported helpers
    "detect_language",
    "extract_file_structure",
    "extract_source_snippet",
    "extract_call_sites",
    "find_enclosing_function",
    "build_dependency_graph",
    "resolve_module_path",
]

# ---------------------------------------------------------------------------
# Supported file extensions (language → set of extensions)
# ---------------------------------------------------------------------------
_SUPPORTED_EXTENSIONS: Dict[str, set] = {
    "python": {".py"},
    # JavaScript / TypeScript / Java are detected but not yet deeply parsed.
    "javascript": {".js", ".jsx", ".mjs"},
    "typescript": {".ts", ".tsx"},
    "java": {".java"},
}

_ALL_SUPPORTED: set = set()
for _exts in _SUPPORTED_EXTENSIONS.values():
    _ALL_SUPPORTED |= _exts

# Directories that should always be skipped during a walk.
_SKIP_DIRS: set = {
    "__pycache__",
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    "egg-info",
}


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------

def parse_codebase(
    root: str,
    *,
    extensions: Optional[set] = None,
    skip_dirs: Optional[set] = None,
) -> Dict[str, dict]:
    """Walk *root* and return a ``{filepath: structure}`` mapping.

    Parameters
    ----------
    root:
        Absolute or relative path to the repository / directory to scan.
    extensions:
        If given, only files whose suffix is in this set will be parsed.
        Defaults to all supported extensions (currently ``.py``).
    skip_dirs:
        Directory *names* (not full paths) to skip.  Merged with the
        built-in skip list (``__pycache__``, ``.git``, ``node_modules``, …).

    Returns
    -------
    dict
        Keys are POSIX-style relative paths (from *root*); values are the
        dicts returned by :func:`ast_extractor.extract_file_structure`.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(f"{root_path} is not a directory")

    allowed = extensions if extensions is not None else _ALL_SUPPORTED
    ignored = _SKIP_DIRS | (skip_dirs or set())

    structures: Dict[str, dict] = {}

    for path in sorted(root_path.rglob("*")):
        # Skip ignored directories (check every parent component).
        if any(part in ignored for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix not in allowed:
            continue

        lang = detect_language(str(path))
        if lang != "python":
            # Only Python is deeply parsed for now; store a stub for others.
            rel = path.relative_to(root_path).as_posix()
            structures[rel] = {
                "filepath": str(path),
                "language": lang,
                "functions": [],
                "classes": [],
                "imports": [],
                "stats": {"total_lines": 0, "code_lines": 0, "comment_lines": 0},
                "parse_error": "deep parsing not yet supported for this language",
            }
            continue

        rel = path.relative_to(root_path).as_posix()
        structures[rel] = extract_file_structure(str(path))

    return structures


def resolve_dependencies(
    file_structures: Dict[str, dict],
    base_dir: str,
) -> List[dict]:
    """Convenience wrapper around :func:`dependency_resolver.build_dependency_graph`.

    Parameters
    ----------
    file_structures:
        Mapping returned by :func:`parse_codebase`.
    base_dir:
        The root directory of the codebase (used for module-path resolution).

    Returns
    -------
    list[dict]
        Each entry describes one dependency edge.  See
        :func:`dependency_resolver.build_dependency_graph` for the schema.
    """
    return build_dependency_graph(file_structures, base_dir=base_dir)
