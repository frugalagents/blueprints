"""
dependency_resolver — Map import statements to concrete source files.

Given the file structures produced by :mod:`ast_extractor`, this module
resolves every ``import`` / ``from … import`` to the file that provides
the imported name.  The result is a dependency graph expressed as a list
of edge dicts.

Only the Python standard library is used.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Module-path resolution
# ---------------------------------------------------------------------------

def resolve_module_path(module_name: str, base_dir: str) -> Optional[str]:
    """Resolve a dotted Python module name to a file path under *base_dir*.

    The function checks for both **package** (``module/__init__.py``) and
    **module** (``module.py``) forms, preferring the package if both exist.

    Relative imports (leading dots) are stripped before resolution — callers
    should normalise relative module names *before* calling this function
    (see :func:`_normalise_relative_import`).

    Parameters
    ----------
    module_name:
        Dotted module name, e.g. ``"parser.ast_extractor"``.
    base_dir:
        Root directory of the codebase.

    Returns
    -------
    str | None
        POSIX-style path relative to *base_dir* if the module was found,
        otherwise ``None``.

    Examples
    --------
    >>> resolve_module_path("parser.ast_extractor", "/repo")
    'parser/ast_extractor.py'    # if that file exists
    """
    if not module_name:
        return None

    base = Path(base_dir).resolve()
    parts = module_name.split(".")
    relative = Path(*parts)

    # Check package form: some/module/__init__.py
    package_init = base / relative / "__init__.py"
    if package_init.is_file():
        return (relative / "__init__.py").as_posix()

    # Check module form: some/module.py
    module_file = base / relative.with_suffix(".py")
    if module_file.is_file():
        return relative.with_suffix(".py").as_posix()

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_relative_import(
    module_name: str,
    level: int,
    importer_path: str,
) -> str:
    """Convert a relative import to an absolute dotted module name.

    Parameters
    ----------
    module_name:
        The module portion of the import (may be empty for ``from . import x``).
    level:
        Number of leading dots (1 for ``.``, 2 for ``..``, etc.).
    importer_path:
        POSIX-style relative path of the file that contains the import,
        e.g. ``"parser/call_graph.py"``.

    Returns
    -------
    str
        Absolute dotted module name.
    """
    if level == 0:
        return module_name

    parts = Path(importer_path).parts
    # Drop the filename to get the package path
    package_parts = list(parts[:-1])
    # Go up (level - 1) directories  (level=1 means current package)
    up = level - 1
    if up > 0:
        package_parts = package_parts[: len(package_parts) - up]

    base = ".".join(package_parts)
    if module_name:
        return f"{base}.{module_name}" if base else module_name
    return base


def _is_stdlib_module(module_name: str) -> bool:
    """Heuristic check whether *module_name* belongs to the standard library.

    This is intentionally conservative — it only returns ``True`` for
    top-level names that appear in ``sys.stdlib_module_names`` (Python 3.10+)
    or a small hard-coded fallback set.
    """
    top = module_name.split(".")[0]
    # Python 3.10+ exposes a definitive set
    if hasattr(sys, "stdlib_module_names"):
        return top in sys.stdlib_module_names
    # Fallback for older Pythons (non-exhaustive but covers the common ones)
    _FALLBACK = {
        "abc", "ast", "asyncio", "base64", "builtins", "collections",
        "concurrent", "contextlib", "copy", "csv", "ctypes", "dataclasses",
        "datetime", "decimal", "difflib", "email", "enum", "fileinput",
        "fnmatch", "fractions", "functools", "getpass", "glob", "gzip",
        "hashlib", "heapq", "hmac", "html", "http", "importlib",
        "inspect", "io", "itertools", "json", "logging", "math",
        "multiprocessing", "operator", "os", "pathlib", "pickle",
        "platform", "pprint", "queue", "random", "re", "shlex", "shutil",
        "signal", "socket", "sqlite3", "statistics", "string",
        "struct", "subprocess", "sys", "tempfile", "textwrap",
        "threading", "time", "timeit", "traceback", "typing",
        "unittest", "urllib", "uuid", "venv", "warnings", "xml",
        "zipfile", "zipimport",
    }
    return top in _FALLBACK


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_dependency_graph(
    file_structures: Dict[str, dict],
    *,
    base_dir: str = ".",
    include_stdlib: bool = False,
    include_external: bool = True,
) -> List[Dict[str, Any]]:
    """Build a dependency graph from extracted file structures.

    Each edge represents one import relationship: *importer* depends on
    *target*.

    Parameters
    ----------
    file_structures:
        Mapping ``{relative_path: structure_dict}`` as produced by
        :func:`parse_codebase <parser.parse_codebase>`.
    base_dir:
        Root directory of the codebase (used for path resolution).
    include_stdlib:
        If ``True``, edges to standard-library modules are included.
        Defaults to ``False`` (they are skipped to reduce noise).
    include_external:
        If ``True``, edges to unresolved third-party packages are included
        (with ``resolved=False``).  Defaults to ``True``.

    Returns
    -------
    list[dict]
        Each dict describes one dependency edge:

        .. code-block:: python

            {
                "source_file": "app/main.py",
                "target_file": "app/utils.py",   # or None
                "import_module": "app.utils",
                "import_name": "helper",
                "import_type": "from_import",     # or "import"
                "line": 3,
                "resolved": True,
                "category": "internal" | "stdlib" | "external",
            }
    """
    edges: List[Dict[str, Any]] = []

    for rel_path, structure in file_structures.items():
        for imp in structure.get("imports", []):
            raw_module: str = imp.get("module", "")
            name: str = imp.get("name", "")
            alias: Optional[str] = imp.get("alias")
            imp_type: str = imp.get("type", "import")
            level: int = imp.get("level", 0)
            line: int = imp.get("line", 0)

            # Normalise relative imports
            if level > 0:
                abs_module = _normalise_relative_import(raw_module, level, rel_path)
            else:
                abs_module = raw_module

            # Classify
            if _is_stdlib_module(abs_module):
                if not include_stdlib:
                    continue
                category = "stdlib"
                target_file = None
                resolved = True  # stdlib is "resolved" but not to a local file
            else:
                # Try to resolve within the codebase
                # For `from foo.bar import baz`, resolve `foo.bar` first,
                # then fall back to `foo.bar.baz` (it might be a sub-module).
                target_file = resolve_module_path(abs_module, base_dir)
                if target_file is None and imp_type == "from_import" and name:
                    target_file = resolve_module_path(
                        f"{abs_module}.{name}" if abs_module else name,
                        base_dir,
                    )
                if target_file is not None:
                    category = "internal"
                    resolved = True
                else:
                    category = "external"
                    resolved = False
                    if not include_external:
                        continue

            edges.append({
                "source_file": rel_path,
                "target_file": target_file,
                "import_module": abs_module,
                "import_name": name,
                "import_alias": alias,
                "import_type": imp_type,
                "line": line,
                "resolved": resolved,
                "category": category,
            })

    return edges
