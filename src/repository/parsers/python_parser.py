"""
Python AST Parser — Phase 3 of Pearl's Repository Intelligence (M3).

Extracts classes, functions, methods, variables, constants, decorators,
docstrings, and imports from Python source files using only the
standard-library ``ast`` module.

Guarantees
----------
* **Never executes user code.**  Analysis is AST-only.
* **Never raises.**  All errors (``SyntaxError``, ``OSError``, encoding
  failures, unexpected exceptions) are captured in
  :attr:`~src.repository.parsers.ParseResult.errors`.
* **Stable output.**  Two successive parses of an unchanged file produce
  identical :class:`~src.repository.parsers.ParseResult` objects.

See :ref:`docs/REPOSITORY_ARCHITECTURE.md` — Phase 3 for the complete
SymbolDef contract and extraction rules.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level pure helpers (no state, reusable by future parsers)
# ---------------------------------------------------------------------------


def _decorator_name(node: ast.expr) -> str:
    """Return the simple string name of a decorator AST node.

    Handles the three common syntactic forms:

    * ``@name``            → ``ast.Name``       → ``"name"``
    * ``@module.name``     → ``ast.Attribute``  → ``"name"``
    * ``@name(...)``       → ``ast.Call``       → recurse on ``func``
    * anything else        → ``ast.unparse()``  as fallback
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "<unknown>"


def _classify_name(name: str) -> SymbolKind:
    """Return ``CONSTANT`` if *name* is ALL_CAPS, else ``VARIABLE``.

    Convention: a name is a constant when every alphabetic character is
    uppercase and there is at least one alphabetic character.  This
    matches ``MAX_RETRIES``, ``HTTP_404``, ``_PRIVATE_CONST``, etc.
    """
    if name and name == name.upper() and any(c.isalpha() for c in name):
        return SymbolKind.CONSTANT
    return SymbolKind.VARIABLE


# ---------------------------------------------------------------------------
# Recursive extraction
# ---------------------------------------------------------------------------


def _extract(
    body: list[ast.stmt],
    parent_qn: str | None,
    in_class: bool,
    extract_vars: bool,
    symbols: list[SymbolDef],
) -> None:
    """Walk *body* and append :class:`~parsers.SymbolDef` objects to *symbols*.

    Parameters
    ----------
    body:
        List of AST statement nodes to process.
    parent_qn:
        Dot-joined qualified name of the enclosing scope, or ``None``
        for top-level definitions.
    in_class:
        ``True`` when *body* is the body of a ``ClassDef`` — functions
        at this level become methods.
    extract_vars:
        ``True`` at module scope and class scope (where variables and
        constants are meaningful); ``False`` inside function bodies
        (local variables are noise).
    symbols:
        Accumulator — symbols are appended in source order.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sym = _make_function(node, parent_qn, in_class)
            symbols.append(sym)
            # Recurse into function body: nested defs are extracted,
            # but variables are not (local variables are noise).
            _extract(
                node.body,
                parent_qn=sym.qualified_name,
                in_class=False,
                extract_vars=False,
                symbols=symbols,
            )

        elif isinstance(node, ast.ClassDef):
            sym = _make_class(node, parent_qn)
            symbols.append(sym)
            # Recurse into class body: class-level variables extracted,
            # and methods are recognised via in_class=True.
            _extract(
                node.body,
                parent_qn=sym.qualified_name,
                in_class=True,
                extract_vars=True,
                symbols=symbols,
            )

        elif extract_vars and isinstance(node, (ast.Assign, ast.AnnAssign)):
            sym = _make_var(node, parent_qn)
            if sym is not None:
                symbols.append(sym)


def _make_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parent_qn: str | None,
    in_class: bool,
) -> SymbolDef:
    qn = f"{parent_qn}.{node.name}" if parent_qn else node.name
    kind = SymbolKind.METHOD if in_class else SymbolKind.FUNCTION
    return SymbolDef(
        name=node.name,
        qualified_name=qn,
        kind=kind,
        line_start=node.lineno,
        line_end=node.end_lineno,  # type: ignore[attr-defined]
        docstring=ast.get_docstring(node),
        decorators=[_decorator_name(d) for d in node.decorator_list],
        is_async=isinstance(node, ast.AsyncFunctionDef),
        parent=parent_qn,
    )


def _make_class(node: ast.ClassDef, parent_qn: str | None) -> SymbolDef:
    qn = f"{parent_qn}.{node.name}" if parent_qn else node.name
    return SymbolDef(
        name=node.name,
        qualified_name=qn,
        kind=SymbolKind.CLASS,
        line_start=node.lineno,
        line_end=node.end_lineno,  # type: ignore[attr-defined]
        docstring=ast.get_docstring(node),
        decorators=[_decorator_name(d) for d in node.decorator_list],
        is_async=False,
        parent=parent_qn,
    )


def _make_var(
    node: ast.Assign | ast.AnnAssign,
    parent_qn: str | None,
) -> SymbolDef | None:
    """Return a SymbolDef for a simple variable/constant assignment.

    Returns ``None`` for tuple-unpacking, chained assignments, or any
    target that is not a bare name — these are skipped conservatively.
    """
    if isinstance(node, ast.Assign):
        # Only handle single-target, single-name assignments.
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return None
        name = node.targets[0].id
        end_line = node.end_lineno  # type: ignore[attr-defined]
    else:  # AnnAssign
        if not isinstance(node.target, ast.Name):
            return None
        name = node.target.id
        end_line = node.end_lineno  # type: ignore[attr-defined]

    qn = f"{parent_qn}.{name}" if parent_qn else name
    return SymbolDef(
        name=name,
        qualified_name=qn,
        kind=_classify_name(name),
        line_start=node.lineno,
        line_end=end_line,
        docstring=None,
        decorators=[],
        is_async=False,
        parent=parent_qn,
    )


def _extract_imports(tree: ast.Module) -> list[str]:
    """Walk the entire tree and return all import strings.

    Imports are collected regardless of scope so that conditional
    imports (``if TYPE_CHECKING:``) and deferred imports inside
    functions are included.  Each string is self-contained and
    human-readable:

    * ``"import os"``
    * ``"from pathlib import Path, PurePath"``
    * ``"from ..utils import helper"``
    """
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            prefix = "." * node.level
            names = ", ".join(alias.name for alias in node.names)
            imports.append(f"from {prefix}{module} import {names}")
    return imports


# ---------------------------------------------------------------------------
# PythonParser
# ---------------------------------------------------------------------------


class PythonParser(BaseParser):
    """Python AST parser — extracts symbols and imports from ``.py`` files.

    Supported extensions: ``.py``, ``.pyi``, ``.pyx``

    Extracted symbols (in source order)
    ------------------------------------
    * Top-level and nested classes
    * Top-level, nested, and class-method functions (sync and async)
    * Module-level and class-level variables and constants
    * All imports (any scope)

    Not extracted
    -------------
    * Local variables inside function bodies
    * Tuple-unpacking or chained assignments
    * Lambda bodies
    * Comprehension variables

    For the complete contract see
    ``docs/REPOSITORY_ARCHITECTURE.md`` — Phase 3.
    """

    @property
    def language(self) -> Language:
        return Language.PYTHON

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".py", ".pyi", ".pyx"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        """Parse *file_info* and return all extracted symbols and imports.

        Never raises — all errors are captured in
        :attr:`~parsers.ParseResult.errors`.
        """
        # ----------------------------------------------------------------
        # 1. Read the source file
        # ----------------------------------------------------------------
        source, read_error = self._read(file_info.path)
        if read_error is not None:
            return ParseResult(file_info=file_info, errors=[read_error])

        # ----------------------------------------------------------------
        # 2. Parse to an AST (pure analysis — no code execution)
        # ----------------------------------------------------------------
        try:
            tree = ast.parse(source, filename=str(file_info.path), type_comments=False)
        except SyntaxError as exc:
            line = exc.lineno or "?"
            return ParseResult(
                file_info=file_info,
                errors=[f"SyntaxError at line {line}: {exc.msg}"],
            )
        except ValueError as exc:
            return ParseResult(
                file_info=file_info,
                errors=[f"ValueError during AST parse: {exc}"],
            )

        # ----------------------------------------------------------------
        # 3. Extract symbols (recursive, parent-tracked)
        # ----------------------------------------------------------------
        symbols: list[SymbolDef] = []
        _extract(
            tree.body,
            parent_qn=None,
            in_class=False,
            extract_vars=True,
            symbols=symbols,
        )

        # ----------------------------------------------------------------
        # 4. Extract imports (single ast.walk pass — handles all scopes)
        # ----------------------------------------------------------------
        imports = _extract_imports(tree)

        return ParseResult(
            file_info=file_info,
            symbols=symbols,
            imports=imports,
            errors=[],
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> tuple[str, str | None]:
        """Read *path*, trying UTF-8 then latin-1.

        Returns ``(source, None)`` on success or ``("", error_string)``
        on failure.
        """
        try:
            return path.read_text(encoding="utf-8"), None
        except UnicodeDecodeError:
            try:
                return path.read_text(encoding="latin-1"), None
            except Exception as exc:  # noqa: BLE001
                return "", f"Encoding error reading {path}: {exc}"
        except OSError as exc:
            return "", f"OSError reading {path}: {exc}"
