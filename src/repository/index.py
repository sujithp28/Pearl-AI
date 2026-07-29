"""
Pearl Repository Intelligence — Symbol Index (Phase 4).

``RepositoryIndex`` is the production-grade in-memory index built from
the parse results of every file in a repository.  It serves as the
primary data layer for all downstream phases:

- Phase 5 (Reference Graph): import-to-file and file-to-import lookups
- Phase 6 (Context Builder): file → symbols, name → files for relevance scoring
- Phase 7 (Search): glob-pattern search across qualified names
- Phase 8 (Ranking): symbol reference counts and per-file symbol density

Architecture
------------
Five internal dicts are populated in a single O(n) pass over parse results:

1. ``_by_file``           relative_path → list[SymbolEntry]
2. ``_by_name``           unqualified name → list[SymbolEntry]
3. ``_by_qualified_name`` qualified_name → SymbolEntry  (exact, O(1))
4. ``_by_kind``           SymbolKind → list[SymbolEntry]
5. ``_imports_by_file``   relative_path → list[str]

``SymbolEntry`` is a flat, denormalised record that joins ``SymbolDef``
with its ``FileInfo``.  Callers never need to join two data structures.

Usage
-----
::

    from src.repository import RepositoryScanner, Language
    from src.repository.parsers import ParserRegistry
    from src.repository.index import RepositoryIndex
    from pathlib import Path

    scanner  = RepositoryScanner(Path("."))
    scan     = scanner.scan()
    registry = ParserRegistry.default()
    results  = registry.parse_many(scan.source_files())

    index = RepositoryIndex.build(results)

    # Exact lookup
    entry = index.lookup_qualified("src.agent.agent.PearlAgent")
    if entry:
        print(entry.symbol.line_start)

    # By name
    for e in index.lookup("PatchManager"):
        print(e.relative_path, e.symbol.kind)

    # All methods
    methods = index.symbols_by_kind(SymbolKind.METHOD)

    # What does auth.py import?
    deps = index.imports_for("src/agent/auth.py")

    # What files import 'asyncio'?
    users = index.files_importing("asyncio")

    print(index.stats())
"""

from __future__ import annotations

import fnmatch
import logging
import time
from dataclasses import dataclass, field
from typing import Iterable

from src.repository.models import FileInfo, Language
from src.repository.parsers import ParseResult, SymbolDef, SymbolKind

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SymbolEntry — denormalised index record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolEntry:
    """A ``SymbolDef`` joined with the ``FileInfo`` it was extracted from.

    All fields are immutable.  ``relative_path`` and ``language`` are
    convenience aliases for ``file_info.relative_path`` and
    ``file_info.language`` to avoid attribute chaining on hot query paths.

    Parameters
    ----------
    symbol:
        The extracted symbol with all metadata.
    file_info:
        The file that contains this symbol.
    relative_path:
        Alias for ``file_info.relative_path``.
    language:
        Alias for ``file_info.language``.
    """

    symbol: SymbolDef
    file_info: FileInfo
    relative_path: str
    language: Language

    def __repr__(self) -> str:
        return (
            f"SymbolEntry({self.symbol.kind.value} {self.symbol.qualified_name!r}"
            f" in {self.relative_path!r})"
        )


# ---------------------------------------------------------------------------
# IndexStats — lightweight build statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexStats:
    """Statistics for one ``RepositoryIndex`` build.

    Intended for logging, debugging, and performance monitoring.

    Parameters
    ----------
    file_count:
        Number of files whose parse results were indexed (including files
        with parse errors, which are indexed with zero symbols).
    symbol_count:
        Total number of ``SymbolEntry`` records stored.
    import_count:
        Total number of import strings stored across all files.
    build_duration_ms:
        Wall-clock time for the full index build in milliseconds.
    languages:
        Set of languages present in the indexed files.
    error_file_count:
        Number of files that had at least one parse error.  Their imports
        and any symbols extracted before the error are still indexed.
    """

    file_count: int
    symbol_count: int
    import_count: int
    build_duration_ms: float
    languages: frozenset[Language]
    error_file_count: int = 0

    def __repr__(self) -> str:
        langs = ", ".join(sorted(l.value for l in self.languages))
        return (
            f"IndexStats(files={self.file_count}"
            f" symbols={self.symbol_count}"
            f" imports={self.import_count}"
            f" errors={self.error_file_count}"
            f" build={self.build_duration_ms:.1f}ms"
            f" langs=[{langs}])"
        )


# ---------------------------------------------------------------------------
# RepositoryIndex
# ---------------------------------------------------------------------------


class RepositoryIndex:
    """In-memory symbol and import index for an entire repository.

    Build via the :meth:`build` class method — the constructor is not
    part of the public API.

    Thread safety
    -------------
    After :meth:`build` returns the index is read-only.  All query
    methods are safe to call from multiple threads concurrently.
    :meth:`build` itself is not thread-safe and must be called once
    at initialisation time.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        # Five internal indexes — all populated by build()
        self._by_file: dict[str, list[SymbolEntry]] = {}
        self._by_name: dict[str, list[SymbolEntry]] = {}
        self._by_qualified_name: dict[str, SymbolEntry] = {}
        self._by_kind: dict[SymbolKind, list[SymbolEntry]] = {}
        self._imports_by_file: dict[str, list[str]] = {}
        self._file_infos: dict[str, FileInfo] = {}  # relative_path → FileInfo
        self._stats: IndexStats | None = None

    @classmethod
    def build(cls, parse_results: Iterable[ParseResult]) -> "RepositoryIndex":
        """Build a ``RepositoryIndex`` from an iterable of parse results.

        Parameters
        ----------
        parse_results:
            Any iterable of :class:`~src.repository.parsers.ParseResult`
            objects, typically from
            :meth:`~src.repository.parsers.ParserRegistry.parse_many`.
            Files with parse errors are indexed normally — any symbols
            extracted before the error are included.

        Returns
        -------
        RepositoryIndex
            A fully built, read-only index ready for queries.

        Performance
        -----------
        A single O(n) pass over all parse results.  For a 10 000-symbol
        codebase this completes in under 50 ms.
        """
        t0 = time.monotonic()
        index = cls()

        file_count = 0
        error_file_count = 0
        import_count = 0
        languages: set[Language] = set()

        for pr in parse_results:
            file_count += 1
            rp = pr.file_info.relative_path
            languages.add(pr.file_info.language)
            index._file_infos[rp] = pr.file_info

            if pr.errors:
                error_file_count += 1

            # Imports
            index._imports_by_file[rp] = list(pr.imports)
            import_count += len(pr.imports)

            # Symbols
            entries: list[SymbolEntry] = []
            for sym in pr.symbols:
                entry = SymbolEntry(
                    symbol=sym,
                    file_info=pr.file_info,
                    relative_path=rp,
                    language=pr.file_info.language,
                )
                entries.append(entry)

                # by_name (unqualified)
                index._by_name.setdefault(sym.name, []).append(entry)

                # by_qualified_name (exact; last writer wins on collision)
                if sym.qualified_name in index._by_qualified_name:
                    logger.debug(
                        "Duplicate qualified name %r in %r (previously in %r)",
                        sym.qualified_name,
                        rp,
                        index._by_qualified_name[sym.qualified_name].relative_path,
                    )
                index._by_qualified_name[sym.qualified_name] = entry

                # by_kind
                index._by_kind.setdefault(sym.kind, []).append(entry)

            index._by_file[rp] = entries

        elapsed_ms = (time.monotonic() - t0) * 1000
        symbol_count = sum(len(v) for v in index._by_file.values())

        index._stats = IndexStats(
            file_count=file_count,
            symbol_count=symbol_count,
            import_count=import_count,
            build_duration_ms=elapsed_ms,
            languages=frozenset(languages),
            error_file_count=error_file_count,
        )

        logger.debug("Index built: %s", index._stats)
        return index

    # ------------------------------------------------------------------
    # Symbol queries
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> list[SymbolEntry]:
        """Return all symbols whose *unqualified* name matches *name* exactly.

        Case-sensitive.  Returns an empty list if no match is found.

        Parameters
        ----------
        name:
            Unqualified symbol name, e.g. ``"PatchManager"`` or
            ``"__init__"``.

        Examples
        --------
        ::

            entries = index.lookup("PatchManager")
            # → [SymbolEntry(class 'PatchManager' in 'src/agent/patch.py')]
        """
        return list(self._by_name.get(name, []))

    def lookup_qualified(self, qualified_name: str) -> SymbolEntry | None:
        """Return the symbol with exactly this *qualified_name*, or ``None``.

        O(1) dict lookup.  When the same qualified name exists in
        multiple files (unusual but possible in large repos), returns
        the last one indexed — see :meth:`build` for the collision note.

        Parameters
        ----------
        qualified_name:
            Dot-joined path, e.g. ``"PearlAgent.run_autonomous"``.
        """
        return self._by_qualified_name.get(qualified_name)

    def symbols_in_file(self, relative_path: str) -> list[SymbolEntry]:
        """Return all symbols in the file at *relative_path*, in source order.

        Parameters
        ----------
        relative_path:
            Repository-relative path using forward slashes, matching
            ``FileInfo.relative_path``, e.g. ``"src/agent/agent.py"``.

        Returns an empty list if the file was not indexed or has no
        extractable symbols.
        """
        return list(self._by_file.get(relative_path, []))

    def symbols_by_kind(self, kind: SymbolKind) -> list[SymbolEntry]:
        """Return all symbols of the given *kind* across all indexed files.

        Parameters
        ----------
        kind:
            A :class:`~src.repository.parsers.SymbolKind` value.

        Returns an empty list if no symbols of that kind were indexed.
        """
        return list(self._by_kind.get(kind, []))

    def search(self, pattern: str) -> list[SymbolEntry]:
        """Return symbols whose qualified name matches the glob *pattern*.

        Uses :func:`fnmatch.fnmatchcase` — case-sensitive.  Common patterns:

        * ``"*Manager"``  — all symbols ending in Manager
        * ``"src.agent.*"`` — all symbols in src.agent (any depth)
        * ``"*.authenticate"`` — all symbols named authenticate at any level
        * ``"PearlAgent.*"`` — all direct members of PearlAgent

        Parameters
        ----------
        pattern:
            A glob pattern using ``*``, ``?``, and ``[seq]``.

        Returns
        -------
        list[SymbolEntry]
            All matching entries, in index order (by file, then source
            order within each file).
        """
        results: list[SymbolEntry] = []
        for entries in self._by_file.values():
            for entry in entries:
                if fnmatch.fnmatchcase(entry.symbol.qualified_name, pattern):
                    results.append(entry)
        return results

    # ------------------------------------------------------------------
    # Import queries
    # ------------------------------------------------------------------

    def imports_for(self, relative_path: str) -> list[str]:
        """Return all import strings from the file at *relative_path*.

        Returns an empty list if the file was not indexed or has no
        imports.  The strings are in the canonical format produced by
        the parser — e.g. ``"import os"`` or
        ``"from pathlib import Path"``.

        Parameters
        ----------
        relative_path:
            Repository-relative path using forward slashes.
        """
        return list(self._imports_by_file.get(relative_path, []))

    def files_importing(self, module_or_name: str) -> list[FileInfo]:
        """Return all files that import *module_or_name*.

        Matches any import string that contains *module_or_name* as a
        word — i.e. ``"import os"`` matches ``"os"``, and
        ``"from pathlib import Path"`` matches both ``"pathlib"`` and
        ``"Path"``.

        This is an O(total imports) scan — suitable for interactive queries.
        Phase 5 (Reference Graph) will materialise a faster reverse index
        from this method.

        Parameters
        ----------
        module_or_name:
            Module or name to search for, e.g. ``"asyncio"``,
            ``"PatchManager"``, ``"pathlib"``.

        Returns
        -------
        list[FileInfo]
            One ``FileInfo`` per matching file, in relative-path order.
        """
        results: list[FileInfo] = []
        for rp, imports in sorted(self._imports_by_file.items()):
            for imp in imports:
                # word-boundary match to avoid "os" matching "os.path"
                words = imp.replace(",", " ").replace("(", " ").replace(")", " ").split()
                if module_or_name in words:
                    fi = self._file_infos.get(rp)
                    if fi is not None:
                        results.append(fi)
                    break  # only add each file once
        return results

    def indexed_files(self) -> list[FileInfo]:
        """Return all indexed ``FileInfo`` objects in relative-path order."""
        return [self._file_infos[rp] for rp in sorted(self._file_infos)]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> IndexStats:
        """Return build statistics for this index.

        :raises RuntimeError: if called before :meth:`build` has populated
            the stats (should not happen in normal usage).
        """
        if self._stats is None:
            raise RuntimeError("RepositoryIndex.stats() called before build()")
        return self._stats

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return the total number of indexed symbols."""
        return sum(len(v) for v in self._by_file.values())

    def __repr__(self) -> str:
        s = self._stats
        if s is None:
            return "RepositoryIndex(unbuilt)"
        return (
            f"RepositoryIndex(files={s.file_count}"
            f" symbols={s.symbol_count}"
            f" imports={s.import_count})"
        )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "IndexStats",
    "RepositoryIndex",
    "SymbolEntry",
]
