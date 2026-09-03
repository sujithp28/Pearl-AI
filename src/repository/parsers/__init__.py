"""
Pearl Repository Intelligence — Language Parser Framework (Phase 2).

This package provides a pluggable parser architecture:

- :class:`SymbolKind` — the syntactic categories a parser can extract
- :class:`SymbolDef` — one extracted symbol with full metadata
- :class:`ParseResult` — the output of one parser run on one file
- :class:`BaseParser` — abstract base class every language parser implements
- :class:`ParserRegistry` — registry mapping languages to parsers

Usage
-----
::

    from src.repository.parsers import ParserRegistry
    from src.repository import RepositoryScanner, Language
    from pathlib import Path

    scanner  = RepositoryScanner(Path("."))
    scan     = scanner.scan()

    registry = ParserRegistry.default()   # pre-loaded with built-in parsers

    # Parse all Python files
    py_files = scan.source_files(Language.PYTHON)
    results  = registry.parse_many(py_files)

    for pr in results:
        classes = [s for s in pr.symbols if s.kind is SymbolKind.CLASS]
        print(f"{pr.file_info.relative_path}: {len(classes)} class(es)")

Extension guide
---------------
To add a new language parser:

1. Subclass :class:`BaseParser` in ``src/repository/parsers/<lang>_parser.py``.
2. Implement :attr:`language`, :attr:`supported_extensions`, and
   :meth:`parse`.
3. Call ``registry.register(MyLangParser())`` — or add the registration
   to :meth:`ParserRegistry.default` for built-in parsers.

The framework never needs to be modified to add a new parser.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from src.repository.models import FileInfo, Language

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Symbol vocabulary
# ---------------------------------------------------------------------------


class SymbolKind(str, Enum):
    """The syntactic category of an extracted symbol.

    Every language parser maps its own AST node types onto these categories.
    The mapping does not need to be perfect — :attr:`UNKNOWN` is available
    for constructs that do not fit neatly into the other categories.
    """

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    MODULE = "module"
    DECORATOR = "decorator"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Symbol definition
# ---------------------------------------------------------------------------


@dataclass
class SymbolDef:
    """Metadata for one extracted symbol.

    All fields are populated by the language parser.  Line numbers are
    1-indexed and refer to the file that was parsed.  :attr:`qualified_name`
    uses dots as separators regardless of the source language, so callers
    always work with a uniform format.

    Parameters
    ----------
    name:
        Unqualified symbol name — e.g. ``"authenticate"``.
    qualified_name:
        Fully qualified dotted path relative to the file —
        e.g. ``"auth.middleware.authenticate"``.
    kind:
        Syntactic category.
    line_start:
        First line of the symbol definition, 1-indexed.
    line_end:
        Last line of the symbol definition, 1-indexed (inclusive).
    docstring:
        Docstring text, stripped of leading/trailing whitespace.
        ``None`` if absent.
    decorators:
        Decorator names applied to this symbol, in source order.
        For Python ``@classmethod`` this would be ``["classmethod"]``.
    is_async:
        ``True`` for ``async def`` functions/methods in languages that
        support the concept.
    parent:
        Qualified name of the enclosing symbol, or ``None`` for
        top-level symbols.  A method inside a class has the class's
        qualified name as its parent.

    Extended metadata (Phase 4 — populated by parsers that support it)
    -------------------------------------------------------------------
    signature:
        Parameter list as a source-level string, e.g.
        ``"(self, path: str, content: str) -> None"``.  Parsers that
        cannot extract this leave it as ``None``.  Used by the Context
        Builder (Phase 6) to enrich LLM prompts and by Search (Phase 7)
        to display call signatures in results.
    return_type:
        Return type annotation as a string — e.g. ``"str | None"``.
        ``None`` when absent or when the parser does not extract it.
        Used by the Reference Graph (Phase 5) for type-level resolution.
    raises:
        Exception type names raised directly in the symbol body — e.g.
        ``["ValueError", "OSError"]``.  Conservative: only explicit
        ``raise SomeException(...)`` statements at the top level of the
        body are captured; re-raises and nested raises are omitted.
        Used by the Context Builder (Phase 6) for error-path analysis.

    Extended metadata (Phase 5 — populated by parsers that support it)
    -------------------------------------------------------------------
    base_classes:
        Names of base classes for ``CLASS`` symbols — e.g.
        ``["Base", "Mixin"]`` for ``class Foo(Base, Mixin):``.
        Simple names are stored as-is; dotted names like
        ``"module.Base"`` are stored with the dot chain preserved.
        Defaults to ``[]`` so existing parsers are backward-compatible.
        Used by the Reference Graph (Phase 5) to build INHERITS edges.
    """

    name: str
    qualified_name: str
    kind: SymbolKind
    line_start: int
    line_end: int
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    parent: str | None = None
    # Phase 4 — extended metadata (optional; defaults preserve backward compat)
    signature: str | None = None
    return_type: str | None = None
    raises: list[str] = field(default_factory=list)
    # Phase 5 — extended metadata (optional; defaults preserve backward compat)
    base_classes: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        async_prefix = "async " if self.is_async else ""
        return (
            f"SymbolDef({async_prefix}{self.kind.value} {self.qualified_name!r}"
            f" L{self.line_start}-{self.line_end})"
        )


# ---------------------------------------------------------------------------
# Parse result
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """The complete output of one parser run on one file.

    A :class:`ParseResult` is always returned — even when parsing fails.
    Parse errors are collected into :attr:`errors` rather than raised, so
    a single broken file never aborts a batch operation.

    Parameters
    ----------
    file_info:
        The file that was parsed.
    symbols:
        All symbols extracted from the file, in source order.
    imports:
        Raw import strings as they appear in the source — e.g.
        ``"from os import path"`` for Python.  Parsers normalise these
        to the minimal canonical form; callers should not rely on the
        exact syntax.
    errors:
        Human-readable descriptions of non-fatal parse errors.  An
        empty list means the file was parsed cleanly.
    """

    file_info: FileInfo
    symbols: list[SymbolDef] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def language(self) -> Language:
        """Language of the parsed file — convenience alias for
        ``file_info.language``."""
        return self.file_info.language

    def __repr__(self) -> str:
        return (
            f"ParseResult({self.file_info.relative_path!r}"
            f" symbols={len(self.symbols)}"
            f" imports={len(self.imports)}"
            f" errors={len(self.errors)})"
        )


# ---------------------------------------------------------------------------
# Base parser
# ---------------------------------------------------------------------------


class BaseParser(ABC):
    """Abstract base class every language parser must subclass.

    Subclasses must implement three things:

    1. :attr:`language` — the :class:`~src.repository.models.Language`
       this parser handles.
    2. :attr:`supported_extensions` — the file extensions this parser
       accepts (lower-cased, with leading dot).
    3. :meth:`parse` — the extraction logic.

    Everything else is provided by the base class.

    Thread safety
    -------------
    Parser instances are stateless after construction and are safe to
    call from multiple threads concurrently.  Parsers must not mutate
    instance state during :meth:`parse`.
    """

    # ------------------------------------------------------------------
    # Abstract interface — subclasses must implement these
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def language(self) -> Language:
        """The primary language this parser handles."""

    @property
    @abstractmethod
    def supported_extensions(self) -> frozenset[str]:
        """File extensions this parser can process.

        All entries must be lower-cased and include the leading dot —
        e.g. ``frozenset({".py", ".pyi"})``.  The registry uses
        :meth:`can_parse` (which checks this set) for capability
        detection.
        """

    @abstractmethod
    def parse(self, file_info: FileInfo) -> ParseResult:
        """Parse *file_info* and return all extracted symbols.

        Contracts
        ---------
        * Must not execute user code — AST / lexer analysis only.
        * Must not raise — catch all exceptions and add them to
          :attr:`ParseResult.errors` instead.
        * Must return a :class:`ParseResult` whose
          :attr:`~ParseResult.file_info` is the *file_info* argument.
        """

    # ------------------------------------------------------------------
    # Provided by base class — subclasses may override
    # ------------------------------------------------------------------

    def can_parse(self, file_info: FileInfo) -> bool:
        """Return ``True`` if this parser can process *file_info*.

        The default implementation checks whether
        ``file_info.extension`` is in :attr:`supported_extensions`.
        Override this method if extension-based detection is
        insufficient for a particular language.
        """
        return file_info.extension in self.supported_extensions

    def __repr__(self) -> str:
        return f"{type(self).__name__}(language={self.language.value!r})"


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------


class ParserRegistry:
    """Registry mapping :class:`~src.repository.models.Language` values
    to :class:`BaseParser` instances.

    Typical lifecycle
    -----------------
    ::

        registry = ParserRegistry.default()   # built-in parsers
        registry.register(MyExtraParser())    # extend as needed

        # Check capability
        if registry.supports(Language.PYTHON):
            result = registry.parse(file_info)

        # Batch parse an entire scan
        scan     = RepositoryScanner(root).scan()
        results  = registry.parse_many(scan.source_files())

    Thread safety
    -------------
    :meth:`register` is not thread-safe and must be called at
    initialisation time before any concurrent :meth:`parse` calls.
    Read operations (:meth:`parse`, :meth:`parse_many`,
    :meth:`supports`, :meth:`parser_for`, :meth:`supported_languages`)
    are safe to call from multiple threads concurrently once registration
    is complete.
    """

    def __init__(self) -> None:
        self._parsers: dict[Language, BaseParser] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, parser: BaseParser) -> None:
        """Register *parser* for its declared language.

        If a parser is already registered for the same language it is
        replaced.  Log at DEBUG level so replacements are traceable
        without being noisy.
        """
        lang = parser.language
        if lang in self._parsers:
            logger.debug(
                "Replacing %s with %s for language %r",
                type(self._parsers[lang]).__name__,
                type(parser).__name__,
                lang.value,
            )
        self._parsers[lang] = parser
        logger.debug(
            "Registered %s for language %r",
            type(parser).__name__,
            lang.value,
        )

    # ------------------------------------------------------------------
    # Capability detection
    # ------------------------------------------------------------------

    def parser_for(self, language: Language) -> BaseParser | None:
        """Return the parser registered for *language*, or ``None``."""
        return self._parsers.get(language)

    def supports(self, language: Language) -> bool:
        """Return ``True`` if a parser is registered for *language*."""
        return language in self._parsers

    def supported_languages(self) -> frozenset[Language]:
        """Return the set of languages that have a registered parser."""
        return frozenset(self._parsers)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse(self, file_info: FileInfo) -> ParseResult | None:
        """Parse *file_info* using the registered parser for its language.

        Returns
        -------
        :class:`ParseResult`
            The parser ran and returned a result (which may include
            errors if the file was syntactically broken).
        ``None``
            No parser is registered for ``file_info.language``.

        Parser exceptions are caught, logged at WARNING level, and
        converted to a :class:`ParseResult` with a single error entry
        so that one broken file never aborts a batch operation.
        """
        parser = self._parsers.get(file_info.language)
        if parser is None:
            return None

        try:
            result = parser.parse(file_info)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Parser %s raised on %s: %s",
                type(parser).__name__,
                file_info.relative_path,
                exc,
                exc_info=True,
            )
            result = ParseResult(
                file_info=file_info,
                errors=[f"{type(exc).__name__}: {exc}"],
            )

        return result

    def parse_many(
        self,
        file_infos: Iterable[FileInfo],
    ) -> list[ParseResult]:
        """Parse all *file_infos* that have a registered parser.

        Files whose language has no registered parser are silently
        skipped — this is the normal case when the registry only covers
        a subset of the languages in a repository.

        Returns
        -------
        list[ParseResult]
            Results in the same order as the input iterable,
            minus the skipped files.
        """
        results: list[ParseResult] = []
        for fi in file_infos:
            result = self.parse(fi)
            if result is not None:
                results.append(result)
        return results

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "ParserRegistry":
        """Return a registry pre-loaded with all built-in parsers.

        Built-in parsers registered by this method:

        * Python (.py, .pyi, .pyx) — AST-based
        * JavaScript (.js, .mjs, .cjs, .jsx) — regex-based
        * TypeScript (.ts, .tsx, .mts, .cts) — regex-based
        * Go (.go) — regex-based
        * Rust (.rs) — regex-based
        * Java (.java) — regex-based

        All non-Python parsers gracefully degrade — they never raise
        and return partial results on malformed input.

        To extend with extra parsers::

            registry = ParserRegistry.default()
            registry.register(MyCustomParser())
        """
        # Import here to avoid circular imports at module load time.
        from src.repository.parsers.python_parser import PythonParser  # noqa: PLC0415
        from src.repository.parsers.js_ts_parser import JavaScriptParser, TypeScriptParser  # noqa: PLC0415
        from src.repository.parsers.go_parser import GoParser  # noqa: PLC0415
        from src.repository.parsers.rust_parser import RustParser  # noqa: PLC0415
        from src.repository.parsers.java_parser import JavaParser  # noqa: PLC0415

        registry = cls()
        registry.register(PythonParser())
        registry.register(JavaScriptParser())
        registry.register(TypeScriptParser())
        registry.register(GoParser())
        registry.register(RustParser())
        registry.register(JavaParser())
        return registry

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return the number of registered parsers."""
        return len(self._parsers)

    def __repr__(self) -> str:
        langs = ", ".join(
            sorted(lang.value for lang in self._parsers)
        )
        return f"ParserRegistry([{langs}])"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "BaseParser",
    "ParseResult",
    "ParserRegistry",
    "SymbolDef",
    "SymbolKind",
]
