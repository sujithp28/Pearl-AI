"""
AST/symbol-aware editing for Pearl.

`SymbolEditor` finds and edits Python functions, classes, and
methods by *name* (via the `ast` module) instead of by line number.
It never re-serializes a whole file through `ast.unparse()` — that
would strip comments and reformat everything — instead it uses each
symbol's precise line range (including decorators) to splice new
text into the *original* source, so everything outside the edited
symbol is preserved byte-for-byte.

The `@tool`-decorated functions below wrap `SymbolEditor` for Python
files, route through the same preview/apply mechanism the other
editing tools use (`edit_tools.get_active_patch_manager()`), and
reuse the existing repository index (`repo_tools.find_symbol`) to
resolve a symbol's file when the caller doesn't already know it.
Non-Python files fall back to a clear error pointing at the existing
text-editing tools (`replace_in_file`, `edit_lines`, `patch_file`),
which remain the supported path for any language SymbolEditor
doesn't understand.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.tools.edit_tools import get_active_patch_manager
from src.tools.file_tools import _ensure_within_workspace
from src.tools.metadata import tool

logger = logging.getLogger(__name__)

PYTHON_SUFFIX = ".py"


class SymbolNotFoundError(LookupError):
    """
    Raised when a requested function, class, or method can't be
    found.
    """


class UnsupportedLanguageError(ValueError):
    """
    Raised when symbol-aware editing is attempted on a file
    SymbolEditor can't parse (not Python, or invalid Python syntax).

    Callers should fall back to the text-based editing tools
    (`replace_in_file`, `edit_lines`, `patch_file`) instead.
    """


@dataclass(slots=True)
class SymbolLocation:
    """
    The precise text span of one symbol definition within a file,
    including any decorators — the natural unit to replace, or to
    insert relative to.
    """

    name: str
    kind: str  # "function" | "class" | "method"
    start_line: int  # 1-indexed, inclusive; includes decorators
    end_line: int  # 1-indexed, inclusive
    source: str  # exact original text of [start_line, end_line]


def _decorated_start_line(node: ast.AST) -> int:
    """
    Return the first line of `node`, including any decorators (whose
    own `lineno` sits *before* the `def`/`class` line they decorate).
    """

    decorators = getattr(node, "decorator_list", [])

    if decorators:
        return min(decorator.lineno for decorator in decorators)

    return node.lineno


class SymbolEditor:
    """
    Finds and edits the functions, classes, and methods in a single
    Python file by name.

    Bound to one file's source at construction time; every lookup or
    edit method here operates purely on that source in memory. This
    class writes nothing to disk itself — the `@tool` functions below
    decide whether to stage the result (preview mode) or write it
    (apply mode), exactly like every other editing tool.
    """

    def __init__(self, path: str, source: str | None = None) -> None:
        self.path = path
        self.source = (
            source if source is not None else Path(path).read_text(encoding="utf-8")
        )
        self._lines = self.source.splitlines(keepends=True)

        try:
            self._tree = ast.parse(self.source, filename=path)
        except SyntaxError as exc:
            raise UnsupportedLanguageError(
                f"Cannot parse {path} as Python: {exc}"
            ) from exc

    # -- Finding -------------------------------------------------------

    def find_function(self, name: str) -> SymbolLocation:
        """
        Find a top-level (module-scope) function definition by name.
        """

        for node in self._tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return self._location(node, "function")

        raise SymbolNotFoundError(f"Function '{name}' not found in {self.path}.")

    def find_class(self, name: str) -> SymbolLocation:
        """
        Find a top-level class definition by name.
        """

        return self._location(self._find_class_node(name), "class")

    def find_method(self, class_name: str, method_name: str) -> SymbolLocation:
        """
        Find a method definition inside a specific top-level class.
        """

        class_node = self._find_class_node(class_name)

        for node in class_node.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            ):
                return self._location(node, "method")

        raise SymbolNotFoundError(
            f"Method '{class_name}.{method_name}' not found in {self.path}."
        )

    def _find_class_node(self, name: str) -> ast.ClassDef:
        for node in self._tree.body:
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node

        raise SymbolNotFoundError(f"Class '{name}' not found in {self.path}.")

    def _find_top_level(self, name: str) -> SymbolLocation:
        """
        Find a top-level function *or* class by name (used by
        `insert_after_symbol` / `insert_before_symbol`, which don't
        need to disambiguate the two).
        """

        for node in self._tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return self._location(node, "function")

            if isinstance(node, ast.ClassDef) and node.name == name:
                return self._location(node, "class")

        raise SymbolNotFoundError(f"Symbol '{name}' not found in {self.path}.")

    def _location(self, node: ast.AST, kind: str) -> SymbolLocation:
        start = _decorated_start_line(node)
        end = node.end_lineno

        return SymbolLocation(
            name=node.name,
            kind=kind,
            start_line=start,
            end_line=end,
            source="".join(self._lines[start - 1 : end]),
        )

    # -- Editing ---------------------------------------------------------

    def replace_function(self, name: str, new_source: str) -> str:
        """
        Replace a top-level function (including its decorators) with
        `new_source`. Returns the file's full updated content.
        """

        return self._splice(self.find_function(name), new_source)

    def replace_class(self, name: str, new_source: str) -> str:
        """
        Replace a top-level class (including its decorators) with
        `new_source`. Returns the file's full updated content.
        """

        return self._splice(self.find_class(name), new_source)

    def replace_method(self, class_name: str, method_name: str, new_source: str) -> str:
        """
        Replace a method within a top-level class with `new_source`,
        preserving its indentation context in the rest of the file.
        Returns the file's full updated content.
        """

        return self._splice(self.find_method(class_name, method_name), new_source)

    def insert_after_symbol(self, name: str, new_source: str) -> str:
        """
        Insert `new_source` immediately after a top-level function or
        class. Returns the file's full updated content.
        """

        location = self._find_top_level(name)

        return self._insert(location.end_line, new_source)

    def insert_before_symbol(self, name: str, new_source: str) -> str:
        """
        Insert `new_source` immediately before a top-level function or
        class. Returns the file's full updated content.
        """

        location = self._find_top_level(name)

        return self._insert(location.start_line - 1, new_source)

    def _splice(self, location: SymbolLocation, new_source: str) -> str:
        new_lines = _as_lines(new_source)

        result = (
            self._lines[: location.start_line - 1]
            + new_lines
            + self._lines[location.end_line :]
        )

        return "".join(result)

    def _insert(self, after_line: int, new_source: str) -> str:
        """
        Insert `new_source` as whole lines right after 0-indexed
        line `after_line` (i.e. after 1-indexed line `after_line`).
        """

        new_lines = _as_lines(new_source)

        result = self._lines[:after_line] + new_lines + self._lines[after_line:]

        return "".join(result)


def _as_lines(source: str) -> list[str]:
    """
    Split `source` into newline-terminated lines, guaranteeing the
    last line ends with `\\n` so whatever follows it in a splice
    starts on its own line.
    """

    text = source if source.endswith("\n") else source + "\n"

    return text.splitlines(keepends=True)


# -- Repository-index-aware path resolution -----------------------------


def _resolve_path(path: str, name: str) -> Path:
    """
    Resolve which file to operate on: `path` if given, or — reusing
    the existing repository index via `repo_tools.find_symbol` — the
    file that already defines `name`, if not.
    """

    if path:
        return _ensure_within_workspace(path)

    from src.tools.repo_tools import find_symbol

    locations = find_symbol(name)

    if not locations:
        raise SymbolNotFoundError(
            f"No file in the workspace defines a symbol named '{name}'."
        )

    return _ensure_within_workspace(locations[0]["file"])


def _load_editor(file_path: Path) -> SymbolEditor:
    if file_path.suffix != PYTHON_SUFFIX:
        raise UnsupportedLanguageError(
            f"Symbol-aware editing only supports Python files; "
            f"'{file_path}' is not a .py file. Use replace_in_file, "
            f"edit_lines, or patch_file instead."
        )

    if not file_path.exists():
        raise FileNotFoundError(str(file_path))

    return SymbolEditor(str(file_path))


def _apply_or_stage(file_path: Path, updated_content: str, summary: str) -> str:
    """
    Route an edit's new full-file content through the same
    preview/apply mechanism every other editing tool uses.
    """

    manager = get_active_patch_manager()

    if manager is not None:
        original = file_path.read_text(encoding="utf-8")
        manager.propose(str(file_path), original, updated_content)

        return f"Preview staged: {summary}"

    file_path.write_text(updated_content, encoding="utf-8")

    from src.tools.repo_tools import refresh_indexed_file

    refresh_indexed_file(str(file_path))

    return summary


# -- Tools ---------------------------------------------------------------


@tool(
    description=(
        "Replace a method's full definition within a specific class "
        "(including decorators) in a Python file, preserving the "
        "rest of the file exactly. Python only — for any other "
        "language use replace_in_file or edit_lines. If `path` is "
        "omitted, the file is located via the repository index."
    ),
    parameters={
        "path": "str",
        "class_name": "str",
        "method_name": "str",
        "new_source": "str",
    },
    returns="str",
)
def replace_method(
    class_name: str, method_name: str, new_source: str, path: str = ""
) -> str:
    file_path = _resolve_path(path, class_name)
    editor = _load_editor(file_path)
    updated = editor.replace_method(class_name, method_name, new_source)

    logger.info(
        "Replacing method '%s.%s' in %s", class_name, method_name, file_path
    )

    return _apply_or_stage(
        file_path,
        updated,
        f"replaced method '{class_name}.{method_name}' in '{file_path}'.",
    )


@tool(
    description=(
        "Find a top-level function definition by name in a Python "
        "file. If `path` is omitted, the file is located via the "
        "repository index."
    ),
    parameters={"path": "str", "name": "str"},
    returns="dict",
)
def find_function(name: str, path: str = "") -> dict[str, Any]:
    file_path = _resolve_path(path, name)
    editor = _load_editor(file_path)
    location = editor.find_function(name)

    logger.info("Found function '%s' in %s", name, file_path)

    return _location_to_dict(location, file_path)


@tool(
    description=(
        "Find a top-level class definition by name in a Python file. "
        "If `path` is omitted, the file is located via the "
        "repository index."
    ),
    parameters={"path": "str", "name": "str"},
    returns="dict",
)
def find_class(name: str, path: str = "") -> dict[str, Any]:
    file_path = _resolve_path(path, name)
    editor = _load_editor(file_path)
    location = editor.find_class(name)

    logger.info("Found class '%s' in %s", name, file_path)

    return _location_to_dict(location, file_path)


@tool(
    description=(
        "Find a method definition within a specific class in a "
        "Python file. If `path` is omitted, the file is located via "
        "the repository index."
    ),
    parameters={"path": "str", "class_name": "str", "method_name": "str"},
    returns="dict",
)
def find_method(class_name: str, method_name: str, path: str = "") -> dict[str, Any]:
    file_path = _resolve_path(path, class_name)
    editor = _load_editor(file_path)
    location = editor.find_method(class_name, method_name)

    logger.info("Found method '%s.%s' in %s", class_name, method_name, file_path)

    return _location_to_dict(location, file_path)


@tool(
    description=(
        "Replace a top-level function's full definition (including "
        "decorators) in a Python file with new source, preserving "
        "the rest of the file exactly. Python only — for any other "
        "language use replace_in_file or edit_lines. If `path` is "
        "omitted, the file is located via the repository index."
    ),
    parameters={"path": "str", "name": "str", "new_source": "str"},
    returns="str",
)
def replace_function(name: str, new_source: str, path: str = "") -> str:
    file_path = _resolve_path(path, name)
    editor = _load_editor(file_path)
    updated = editor.replace_function(name, new_source)

    logger.info("Replacing function '%s' in %s", name, file_path)

    return _apply_or_stage(
        file_path, updated, f"replaced function '{name}' in '{file_path}'."
    )


@tool(
    description=(
        "Replace a top-level class's full definition (including "
        "decorators) in a Python file with new source, preserving "
        "the rest of the file exactly. Python only — for any other "
        "language use replace_in_file or edit_lines. If `path` is "
        "omitted, the file is located via the repository index."
    ),
    parameters={"path": "str", "name": "str", "new_source": "str"},
    returns="str",
)
def replace_class(name: str, new_source: str, path: str = "") -> str:
    file_path = _resolve_path(path, name)
    editor = _load_editor(file_path)
    updated = editor.replace_class(name, new_source)

    logger.info("Replacing class '%s' in %s", name, file_path)

    return _apply_or_stage(
        file_path, updated, f"replaced class '{name}' in '{file_path}'."
    )


@tool(
    description=(
        "Insert new source code immediately after a named top-level "
        "function or class in a Python file. Python only — for any "
        "other language use replace_in_file or edit_lines. If `path` "
        "is omitted, the file is located via the repository index."
    ),
    parameters={"path": "str", "name": "str", "new_source": "str"},
    returns="str",
)
def insert_after_symbol(name: str, new_source: str, path: str = "") -> str:
    file_path = _resolve_path(path, name)
    editor = _load_editor(file_path)
    updated = editor.insert_after_symbol(name, new_source)

    logger.info("Inserting after symbol '%s' in %s", name, file_path)

    return _apply_or_stage(
        file_path, updated, f"inserted code after '{name}' in '{file_path}'."
    )


@tool(
    description=(
        "Insert new source code immediately before a named top-level "
        "function or class in a Python file. Python only — for any "
        "other language use replace_in_file or edit_lines. If `path` "
        "is omitted, the file is located via the repository index."
    ),
    parameters={"path": "str", "name": "str", "new_source": "str"},
    returns="str",
)
def insert_before_symbol(name: str, new_source: str, path: str = "") -> str:
    file_path = _resolve_path(path, name)
    editor = _load_editor(file_path)
    updated = editor.insert_before_symbol(name, new_source)

    logger.info("Inserting before symbol '%s' in %s", name, file_path)

    return _apply_or_stage(
        file_path,
        updated,
        f"inserted code before '{name}' in '{file_path}'.",
    )


def _location_to_dict(location: SymbolLocation, file_path: Path) -> dict[str, Any]:
    return {
        "file": str(file_path),
        "name": location.name,
        "kind": location.kind,
        "start_line": location.start_line,
        "end_line": location.end_line,
        "source": location.source,
    }
