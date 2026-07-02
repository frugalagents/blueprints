"""
ast_extractor — Extract structural metadata from Python source files.

Uses only the built-in :mod:`ast` module so there are **zero external
dependencies**.  The primary entry point is :func:`extract_file_structure`
which returns a rich dict describing every function, class, import, and
some file-level statistics.

Design goals
------------
* No side-effects — every function is pure (reads files, returns data).
* Defensive — malformed / unparsable files return a meaningful error dict
  rather than raising.
* Extensible — the ``detect_language`` hook and the per-language branch in
  ``extract_file_structure`` make it straightforward to plug in parsers for
  other languages later.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

#: Map of file extensions → canonical language name.
_EXTENSION_MAP: Dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".php": "php",
    ".r": "r",
    ".R": "r",
    ".lua": "lua",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
}


def detect_language(filepath: str) -> Optional[str]:
    """Return the canonical language name for *filepath*, or ``None``.

    Detection is purely extension-based, which is fast and dependency-free.

    >>> detect_language("app/main.py")
    'python'
    >>> detect_language("src/utils.ts")
    'typescript'
    >>> detect_language("Makefile") is None
    True
    """
    suffix = Path(filepath).suffix
    return _EXTENSION_MAP.get(suffix)


# ---------------------------------------------------------------------------
# Source-snippet extraction
# ---------------------------------------------------------------------------

def extract_source_snippet(filepath: str, start_line: int, end_line: int) -> str:
    """Return the source text between *start_line* and *end_line* (1-indexed, inclusive).

    Parameters
    ----------
    filepath:
        Path to the source file.
    start_line:
        First line to include (1-indexed).
    end_line:
        Last line to include (1-indexed, inclusive).

    Returns
    -------
    str
        The extracted lines joined by newlines.  Trailing whitespace on each
        line is preserved; a single trailing newline is stripped from the
        result for tidiness.

    Raises
    ------
    FileNotFoundError
        If *filepath* does not exist.
    ValueError
        If *start_line* or *end_line* are out of range.
    """
    path = Path(filepath)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise ValueError(
            f"Line range [{start_line}, {end_line}] is out of bounds for "
            f"{filepath} ({len(lines)} lines)"
        )
    snippet = "".join(lines[start_line - 1 : end_line])
    return snippet.rstrip("\n")


# ---------------------------------------------------------------------------
# Internal AST helpers
# ---------------------------------------------------------------------------

def _unparse_annotation(node: Optional[ast.expr]) -> Optional[str]:
    """Best-effort pretty-print of a type-annotation AST node.

    Returns ``None`` when *node* is ``None``.
    """
    if node is None:
        return None
    try:
        # ast.unparse is available from Python 3.9+
        return ast.unparse(node)
    except Exception:
        return repr(node)


def _decorator_names(decorator_list: List[ast.expr]) -> List[str]:
    """Extract a human-readable name for each decorator."""
    names: List[str] = []
    for dec in decorator_list:
        try:
            names.append(ast.unparse(dec))
        except Exception:
            if isinstance(dec, ast.Name):
                names.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                names.append(f"...{dec.attr}")
            else:
                names.append("<unknown>")
    return names


def _extract_args(node: ast.arguments) -> List[Dict[str, Any]]:
    """Return a list of argument dicts from a ``FunctionDef.args`` node."""
    result: List[Dict[str, Any]] = []
    all_args: List[ast.arg] = (
        node.posonlyargs + node.args + node.kwonlyargs
    )
    if node.vararg:
        all_args.append(node.vararg)
    if node.kwarg:
        all_args.append(node.kwarg)

    for arg in all_args:
        entry: Dict[str, Any] = {"name": arg.arg}
        if arg.annotation:
            entry["annotation"] = _unparse_annotation(arg.annotation)
        # Identify *args / **kwargs
        if arg is node.vararg:
            entry["kind"] = "vararg"
        elif arg is node.kwarg:
            entry["kind"] = "kwarg"
        elif arg in node.posonlyargs:
            entry["kind"] = "positional_only"
        elif arg in node.kwonlyargs:
            entry["kind"] = "keyword_only"
        else:
            entry["kind"] = "regular"
        result.append(entry)
    return result


def _extract_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: List[str],
) -> Dict[str, Any]:
    """Build a metadata dict for a single function/method node."""
    start = node.lineno
    end = node.end_lineno or node.lineno
    return {
        "name": node.name,
        "args": _extract_args(node.args),
        "return_type": _unparse_annotation(node.returns),
        "decorators": _decorator_names(node.decorator_list),
        "docstring": ast.get_docstring(node),
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "start_line": start,
        "end_line": end,
        "source": "\n".join(source_lines[start - 1 : end]),
    }


def _extract_class(
    node: ast.ClassDef,
    source_lines: List[str],
) -> Dict[str, Any]:
    """Build a metadata dict for a single class node."""
    bases = []
    for base in node.bases:
        try:
            bases.append(ast.unparse(base))
        except Exception:
            bases.append("<unknown>")

    methods: List[Dict[str, Any]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(_extract_function(child, source_lines))

    return {
        "name": node.name,
        "bases": bases,
        "decorators": _decorator_names(node.decorator_list),
        "docstring": ast.get_docstring(node),
        "methods": methods,
        "start_line": node.lineno,
        "end_line": node.end_lineno or node.lineno,
    }


def _extract_import(node: ast.Import | ast.ImportFrom) -> List[Dict[str, Any]]:
    """Return one or more import dicts from an import node."""
    results: List[Dict[str, Any]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            results.append({
                "module": alias.name,
                "name": alias.asname or alias.name,
                "alias": alias.asname,
                "type": "import",
                "line": node.lineno,
            })
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        level = node.level or 0
        for alias in node.names:
            results.append({
                "module": ("." * level) + module,
                "name": alias.name,
                "alias": alias.asname,
                "type": "from_import",
                "level": level,
                "line": node.lineno,
            })
    return results


def _compute_line_stats(source: str) -> Dict[str, int]:
    """Compute simple line-count statistics for a source string.

    Returns
    -------
    dict
        ``total_lines`` — total number of lines (including blanks).
        ``code_lines``  — lines that are not blank and not pure comments.
        ``comment_lines`` — lines whose first non-whitespace char is ``#``.
        ``blank_lines`` — empty / whitespace-only lines.
    """
    lines = source.splitlines()
    total = len(lines)
    blank = 0
    comment = 0
    code = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
        elif stripped.startswith("#"):
            comment += 1
        else:
            code += 1
    return {
        "total_lines": total,
        "code_lines": code,
        "comment_lines": comment,
        "blank_lines": blank,
    }


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def extract_file_structure(filepath: str) -> Dict[str, Any]:
    """Parse a source file and return its structural metadata.

    Currently only **Python** files are deeply parsed.  For other detected
    languages a stub dict is returned with ``parse_error`` explaining why.

    Parameters
    ----------
    filepath:
        Absolute or relative path to the source file.

    Returns
    -------
    dict
        A dictionary with keys:

        * ``filepath`` — canonical path string.
        * ``language`` — detected language (e.g. ``"python"``).
        * ``functions`` — list of top-level function dicts.
        * ``classes`` — list of class dicts (each with nested ``methods``).
        * ``imports`` — list of import dicts.
        * ``stats`` — ``{total_lines, code_lines, comment_lines, blank_lines}``.
        * ``module_docstring`` — the file-level docstring, if any.
        * ``parse_error`` — present **only** when parsing failed; contains
          the error message.

    Raises
    ------
    FileNotFoundError
        If *filepath* does not exist.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(filepath)

    lang = detect_language(filepath)
    if lang is None:
        return {
            "filepath": str(path),
            "language": None,
            "functions": [],
            "classes": [],
            "imports": [],
            "stats": {"total_lines": 0, "code_lines": 0, "comment_lines": 0, "blank_lines": 0},
            "parse_error": "unsupported or unrecognised file extension",
        }

    if lang != "python":
        source = path.read_text(encoding="utf-8", errors="replace")
        return {
            "filepath": str(path),
            "language": lang,
            "functions": [],
            "classes": [],
            "imports": [],
            "stats": _compute_line_stats(source),
            "parse_error": f"deep parsing not yet implemented for '{lang}'",
        }

    # ---- Python deep parse ------------------------------------------------
    source = path.read_text(encoding="utf-8", errors="replace")
    source_lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return {
            "filepath": str(path),
            "language": "python",
            "functions": [],
            "classes": [],
            "imports": [],
            "stats": _compute_line_stats(source),
            "parse_error": f"SyntaxError: {exc}",
        }

    functions: List[Dict[str, Any]] = []
    classes: List[Dict[str, Any]] = []
    imports: List[Dict[str, Any]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_extract_function(node, source_lines))
        elif isinstance(node, ast.ClassDef):
            classes.append(_extract_class(node, source_lines))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.extend(_extract_import(node))

    return {
        "filepath": str(path),
        "language": "python",
        "module_docstring": ast.get_docstring(tree),
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "stats": _compute_line_stats(source),
    }
