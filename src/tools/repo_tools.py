"""
Repository intelligence tools for Pearl.

These tools index Python source files in the workspace and let Pearl
locate symbol definitions, find textual references to a symbol,
run general text search, and summarize the project layout.
"""

from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.tools.file_tools import _ensure_within_workspace
from src.tools.metadata import tool

logger = logging.getLogger(__name__)

SOURCE_EXTENSIONS = (".py",)

#: Ceiling on results from `search_text` / `find_references`. Sized to
#: stay well inside a planning prompt's context budget: these results
#: are frequently fed straight back to the model, so an uncapped broad
#: match is a latency and correctness problem, not just a big list.
MAX_SEARCH_RESULTS = 200

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".tox",
    ".eggs",
}


def _ensure_workspace_dir(path: str) -> Path:
    """
    Resolve `path` within the workspace and ensure it is a directory.
    """

    root = _ensure_within_workspace(path)

    if not root.exists():
        raise FileNotFoundError(path)

    if not root.is_dir():
        raise NotADirectoryError(path)

    return root


def _iter_files(
    root: Path,
    extensions: tuple[str, ...] | None,
) -> list[Path]:
    """
    Recursively list files under `root`, skipping noisy directories.

    Prunes `IGNORED_DIRS` during the walk (via `os.walk`'s in-place
    `dirnames` filtering) rather than after globbing everything, so
    large ignored trees like `.venv` or `node_modules` are never
    descended into or stat'd in the first place.
    """

    files: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]

        for filename in filenames:
            if extensions is not None and Path(filename).suffix not in extensions:
                continue

            files.append(Path(dirpath) / filename)

    files.sort()

    return files


def _parse_python(file_path: Path) -> ast.AST | None:
    """
    Parse `file_path` as Python source, or return None (logging a
    warning) if it can't be read or parsed.
    """

    try:
        source = file_path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        logger.warning("Skipping unparsable file %s: %s", file_path, exc)
        return None


def _extract_definitions_and_imports(
    tree: ast.AST,
) -> tuple[list[tuple[str, int, str]], list[str]]:
    """
    Walk `tree` once and return `(definitions, imports)`, where each
    definition is `(name, line, "function" | "class")` and each
    import is a dotted module/name string (e.g. `"pathlib.Path"`).
    """

    definitions: list[tuple[str, int, str]] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            definitions.append((node.name, node.lineno, kind))
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            prefix = "." * node.level
            imports.extend(
                f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}"
                for alias in node.names
            )

    return definitions, imports


@dataclass
class RepositoryIndex:
    """
    A cached snapshot of one workspace directory: its source files,
    the functions/classes defined in them, and their imports.
    """

    root: Path
    files: list[str] = field(default_factory=list)
    symbols: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    imports: dict[str, list[str]] = field(default_factory=dict)


# Indexed once per workspace root and reused by find_symbol,
# find_references-adjacent lookups, index_repository, and
# summarize_project, instead of re-walking and re-parsing the
# filesystem on every call. Primed on server startup by
# `build_startup_index()`, and otherwise built lazily on first use.
_INDEX_CACHE: dict[Path, RepositoryIndex] = {}


def _build_index(root: Path) -> RepositoryIndex:
    """
    Parse every source file under `root` once and build a
    `RepositoryIndex` of its files, symbols, and imports.
    """

    files = _iter_files(root, SOURCE_EXTENSIONS)
    symbols: dict[str, list[dict[str, Any]]] = {}
    imports: dict[str, list[str]] = {}

    for file_path in files:
        tree = _parse_python(file_path)

        if tree is None:
            continue

        rel = file_path.relative_to(root).as_posix()
        definitions, file_imports = _extract_definitions_and_imports(tree)

        for name, line, kind in definitions:
            symbols.setdefault(name, []).append(
                {"file": rel, "line": line, "type": kind}
            )

        if file_imports:
            imports[rel] = file_imports

    return RepositoryIndex(
        root=root,
        files=[file_path.relative_to(root).as_posix() for file_path in files],
        symbols=symbols,
        imports=imports,
    )


def _get_index(root: Path) -> RepositoryIndex:
    """
    Return the cached `RepositoryIndex` for `root`, building it on
    first access.
    """

    index = _INDEX_CACHE.get(root)

    if index is None:
        index = _build_index(root)
        _INDEX_CACHE[root] = index

    return index


def _index_symbols(root: Path) -> dict[str, list[dict[str, Any]]]:
    """
    Return the symbol index (name -> definition sites) for `root`.
    """

    return _get_index(root).symbols


def get_repository_index(path: str = ".") -> RepositoryIndex:
    """
    Return the (cached) `RepositoryIndex` for `path`, building it on
    first access.

    Public, unlike `_get_index`, so other components (e.g.
    `ContextManager`) can reuse the same index instead of
    reimplementing indexing — Pearl has exactly one indexing system.
    """

    root = _ensure_workspace_dir(path)

    return _get_index(root)


def refresh_indexed_file(path: str) -> None:
    """
    Update every cached `RepositoryIndex` that covers `path` after it
    was created, modified, or deleted on disk, so `find_symbol`/
    `find_references`/`ContextBuilder` stay correct for the rest of
    the session instead of reading a pre-edit snapshot.

    Re-parses only this one file (or, if it no longer exists, drops
    its entries) and patches the affected index's `files`/`symbols`/
    `imports` in place — cost is proportional to one file, not the
    repository, and no directory walk happens. If `path` isn't
    Python, or isn't covered by any currently-cached index (nothing
    has indexed that workspace root yet), this is a no-op.

    Best-effort: never raises. Editing tools call this right after a
    write that has already succeeded on disk — a refresh failure
    should leave the cache stale, not undo or fail that write.
    """

    try:
        file_path = Path(path).resolve()
    except OSError:
        return

    for root, index in _INDEX_CACHE.items():
        try:
            rel = file_path.relative_to(root).as_posix()
        except ValueError:
            continue

        try:
            _refresh_index_entry(index, rel, file_path)
        except Exception:
            logger.warning(
                "Failed to refresh repository index entry for %s; it "
                "may be stale until this process reindexes.",
                rel,
                exc_info=True,
            )


def _refresh_index_entry(index: RepositoryIndex, rel: str, file_path: Path) -> None:
    """
    Drop `rel`'s previous entry from `index`, then re-add it from a
    fresh parse if the file still exists and is still Python source.
    """

    index.symbols = {
        name: kept
        for name, locations in index.symbols.items()
        if (kept := [loc for loc in locations if loc["file"] != rel])
    }
    index.imports.pop(rel, None)

    if rel in index.files:
        index.files.remove(rel)

    if not file_path.exists() or file_path.suffix not in SOURCE_EXTENSIONS:
        return

    tree = _parse_python(file_path)

    if tree is None:
        return

    index.files.append(rel)
    index.files.sort()

    definitions, file_imports = _extract_definitions_and_imports(tree)

    for name, line, kind in definitions:
        index.symbols.setdefault(name, []).append(
            {"file": rel, "line": line, "type": kind}
        )

    if file_imports:
        index.imports[rel] = file_imports


def _search(
    root: Path,
    pattern: re.Pattern[str],
    extensions: tuple[str, ...] | None,
    limit: int = MAX_SEARCH_RESULTS,
) -> list[dict[str, Any]]:
    """
    Search files under `root` for lines matching `pattern`, returning
    at most `limit` matches.

    The cap is not a performance tweak: an uncapped broad query (say
    `self`) on a large repository returns megabytes of matches that go
    straight into the next planning prompt, blowing the context budget
    and the latency with it. Truncation is reported to the caller
    rather than silently swallowed, so the model is told its view is
    partial instead of assuming it saw everything.
    """

    matches: list[dict[str, Any]] = []

    for file_path in _iter_files(root, extensions):
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        for line_number, line in enumerate(lines, start=1):
            if pattern.search(line):
                if len(matches) >= limit:
                    return matches

                matches.append(
                    {
                        "file": file_path.relative_to(root).as_posix(),
                        "line": line_number,
                        "text": line.strip(),
                    }
                )

    return matches


def _truncation_notice(matches: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    """
    A sentinel row appended when results were capped, so the caller —
    and the model reading the result — knows the list is incomplete
    and the query should be narrowed.
    """

    return {
        "file": "",
        "line": 0,
        "text": (
            f"[truncated at {limit} matches — narrow the query for complete results]"
        ),
    }


@tool(
    description=(
        "Index Python symbols (functions and classes) under a "
        "directory and return a summary of the index."
    ),
    parameters={
        "path": "str",
    },
    returns="dict",
    risk_level="safe",
)
def index_repository(path: str = ".") -> dict[str, Any]:
    """
    Build a symbol index for `path` and return summary counts.
    """

    root = _ensure_workspace_dir(path)

    logger.info("Indexing repository: %s", root)

    files = _iter_files(root, SOURCE_EXTENSIONS)
    index = _index_symbols(root)

    symbols_found = sum(len(locations) for locations in index.values())

    return {
        "root": str(root),
        "files_indexed": len(files),
        "unique_symbols": len(index),
        "symbols_found": symbols_found,
    }


@tool(
    description=(
        "Find where a symbol (function or class) is defined in the workspace."
    ),
    parameters={
        "name": "str",
        "path": "str",
    },
    returns="list[dict]",
    risk_level="safe",
)
def find_symbol(name: str, path: str = ".") -> list[dict[str, Any]]:
    """
    Return every definition site for `name`.
    """

    if not name:
        raise ValueError("`name` must not be empty.")

    root = _ensure_workspace_dir(path)

    logger.info("Finding symbol: %s", name)

    return _index_symbols(root).get(name, [])


@tool(
    description=(
        "Find every textual reference to a symbol across the workspace's source files."
    ),
    parameters={
        "symbol": "str",
        "path": "str",
    },
    returns="list[dict]",
    risk_level="safe",
)
def find_references(
    symbol: str,
    path: str = ".",
) -> list[dict[str, Any]]:
    """
    Return every source line that mentions `symbol` as a whole word.
    """

    if not symbol:
        raise ValueError("`symbol` must not be empty.")

    root = _ensure_workspace_dir(path)

    logger.info("Finding references to: %s", symbol)

    pattern = re.compile(rf"\b{re.escape(symbol)}\b")

    matches = _search(root, pattern, SOURCE_EXTENSIONS)

    if len(matches) >= MAX_SEARCH_RESULTS:
        matches.append(_truncation_notice(matches, MAX_SEARCH_RESULTS))

    return matches


@tool(
    description="Search all workspace files for lines matching a text query.",
    parameters={
        "query": "str",
        "path": "str",
    },
    returns="list[dict]",
    risk_level="safe",
)
def search_text(query: str, path: str = ".") -> list[dict[str, Any]]:
    """
    Return every line containing `query` (plain substring match)
    across all files under `path`.
    """

    if not query:
        raise ValueError("`query` must not be empty.")

    root = _ensure_workspace_dir(path)

    logger.info("Searching text: %s", query)

    pattern = re.compile(re.escape(query))

    matches = _search(root, pattern, None)

    if len(matches) >= MAX_SEARCH_RESULTS:
        matches.append(_truncation_notice(matches, MAX_SEARCH_RESULTS))

    return matches


@tool(
    description=(
        "Summarize the project: file counts, symbol counts, and top-level layout."
    ),
    parameters={
        "path": "str",
    },
    returns="dict",
    risk_level="safe",
)
def summarize_project(path: str = ".") -> dict[str, Any]:
    """
    Return a high-level summary of the project rooted at `path`.
    """

    root = _ensure_workspace_dir(path)

    logger.info("Summarizing project: %s", root)

    all_files = _iter_files(root, None)
    source_files = [
        file_path for file_path in all_files if file_path.suffix in SOURCE_EXTENSIONS
    ]
    index = _index_symbols(root)

    files_by_extension: dict[str, int] = {}

    for file_path in all_files:
        suffix = file_path.suffix or "(no extension)"
        files_by_extension[suffix] = files_by_extension.get(suffix, 0) + 1

    total_lines = 0

    for file_path in source_files:
        try:
            total_lines += len(file_path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            continue

    functions = sum(
        1
        for locations in index.values()
        for location in locations
        if location["type"] == "function"
    )

    classes = sum(
        1
        for locations in index.values()
        for location in locations
        if location["type"] == "class"
    )

    return {
        "root": str(root),
        "total_files": len(all_files),
        "source_files": len(source_files),
        "total_lines": total_lines,
        "files_by_extension": files_by_extension,
        "unique_symbols": len(index),
        "functions": functions,
        "classes": classes,
        "top_level_entries": sorted(item.name for item in root.iterdir()),
    }


@tool(
    description=(
        "Explain a source file: its imports, classes, and functions, with line numbers."
    ),
    parameters={
        "path": "str",
    },
    returns="dict",
    risk_level="safe",
)
def explain_file(path: str) -> dict[str, Any]:
    """
    Return a structural breakdown of a single file: its imports,
    classes, and functions (with line numbers), plus its line count.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    if not file_path.is_file():
        raise IsADirectoryError(path)

    logger.info("Explaining file: %s", file_path)

    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Cannot read {path} as UTF-8 text.") from exc

    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    imports: list[str] = []

    if file_path.suffix in SOURCE_EXTENSIONS:
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as exc:
            raise ValueError(f"Cannot parse {path}: {exc}") from exc

        definitions, imports = _extract_definitions_and_imports(tree)

        for name, line, kind in definitions:
            target = classes if kind == "class" else functions
            target.append({"name": name, "line": line})

    return {
        "file": path,
        "total_lines": len(source.splitlines()),
        "imports": imports,
        "classes": classes,
        "functions": functions,
    }


def build_startup_index(path: str = ".") -> None:
    """
    Eagerly build and cache the repository index for `path` (the
    workspace root by default), so the first `find_symbol` /
    `find_references` / `explain_file` call doesn't pay the parsing
    cost. Reuses `index_repository()` — the same tool a client could
    call directly — purely for its side effect of populating
    `_INDEX_CACHE`.

    Failures (e.g. an inaccessible path) are logged, not raised: a
    stale or missing startup index degrades repository-intelligence
    tools to their existing on-demand behavior rather than blocking
    server startup.
    """

    try:
        summary = index_repository(path)
    except OSError as exc:
        logger.warning("Could not build startup repository index: %s", exc)
        return

    logger.info(
        "Startup repository index built: %d files, %d symbols.",
        summary["files_indexed"],
        summary["unique_symbols"],
    )
