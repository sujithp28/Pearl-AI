"""
Pearl Repository Intelligence — Session-scoped service (Phase 6).

``RepositoryService`` manages the full M3 repository-intelligence stack
(scan → parse → index → graph) for one workspace root.  It is the single
place where these builds happen and the single cache for their results.

Two contracts:

1. **Lazy build** — the scan, parse, index, and graph construction happen
   only on the first access to ``.index`` or ``.graph``, not at
   construction time.  Pearl's MCP server is single-threaded for
   planning, so this avoids the build cost on every server start; it
   only runs when context is first needed.

2. **Session cache** — ``RepositoryService.get_or_build(root)`` returns
   the same service object for the same resolved root path within one
   process lifetime.  This prevents redundant scans when several
   components independently ask for the service.

Usage::

    from src.repository.service import RepositoryService
    from pathlib import Path

    svc = RepositoryService.get_or_build(Path("."))
    print(svc.index.stats())
    print(svc.graph.stats())

To invalidate after a file write::

    svc.invalidate()   # next .index / .graph access rebuilds

To clear all cached services (tests)::

    RepositoryService.clear_cache()
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import ClassVar

from src.repository.graph import RepositoryGraph
from src.repository.index import RepositoryIndex
from src.repository.models import Language
from src.repository.parsers import ParserRegistry
from src.repository.scanner import RepositoryScanner

logger = logging.getLogger(__name__)


class RepositoryService:
    """Session-scoped cache for M3 ``RepositoryIndex`` + ``RepositoryGraph``.

    Build via :meth:`get_or_build` — the constructor is not part of the
    public API.

    Thread safety
    -------------
    Not thread-safe.  Pearl's MCP server plans on a single thread, so
    this is intentional.  Do not call :meth:`invalidate` concurrently
    with property accesses.
    """

    _cache: ClassVar[dict[Path, "RepositoryService"]] = {}

    def __init__(self, root: Path) -> None:
        self._root = root
        self._index: RepositoryIndex | None = None
        self._graph: RepositoryGraph | None = None

    # ------------------------------------------------------------------
    # Factory and cache management
    # ------------------------------------------------------------------

    @classmethod
    def get_or_build(cls, root: Path) -> "RepositoryService":
        """Return the cached ``RepositoryService`` for *root*, creating it if needed.

        Parameters
        ----------
        root:
            Workspace root directory.  Resolved to an absolute path
            before caching so ``Path(".")`` and ``Path("/abs/path")``
            that point to the same directory share one service.
        """
        resolved = root.resolve()
        if resolved not in cls._cache:
            cls._cache[resolved] = cls(resolved)
        return cls._cache[resolved]

    @classmethod
    def clear_cache(cls) -> None:
        """Discard all cached services.  Intended for tests."""
        cls._cache.clear()

    # ------------------------------------------------------------------
    # Properties — lazy-built on first access
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        """Resolved absolute path of the workspace root."""
        return self._root

    @property
    def index(self) -> RepositoryIndex:
        """M3 Phase 4 symbol/import index — built once, cached."""
        if self._index is None:
            self._build()
        return self._index  # type: ignore[return-value]

    @property
    def graph(self) -> RepositoryGraph:
        """M3 Phase 5 reference graph — built once, cached."""
        if self._graph is None:
            self._build()
        return self._graph  # type: ignore[return-value]

    def is_ready(self) -> bool:
        """Return True if the index and graph have already been built."""
        return self._index is not None and self._graph is not None

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        """Discard the cached index and graph.

        The next access to :attr:`index` or :attr:`graph` will trigger
        a full rebuild — scan, parse, index, and graph.  Call this after
        writing one or more files so the service reflects current state.
        """
        self._index = None
        self._graph = None
        logger.debug("RepositoryService invalidated for %s.", self._root)

    # ------------------------------------------------------------------
    # Internal build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """Run scan → parse → index → graph and cache the results."""
        t0 = time.monotonic()

        scanner = RepositoryScanner(self._root)
        scan = scanner.scan()

        registry = ParserRegistry.default()
        results = list(registry.parse_many(scan.source_files(Language.PYTHON)))

        self._index = RepositoryIndex.build(results)
        self._graph = RepositoryGraph.build(self._index)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "RepositoryService built %s in %.1f ms: %s, %s",
            self._root,
            elapsed_ms,
            self._index.stats(),
            self._graph.stats(),
        )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        state = "ready" if self.is_ready() else "unbuilt"
        return f"RepositoryService({self._root!s}, {state})"
