"""
Pearl Repository Intelligence — Reference Graph (Phase 5).

``RepositoryGraph`` is a directed, typed graph of file and symbol
relationships built from a ``RepositoryIndex``.  It answers structural
questions that a flat index cannot:

- What would break if I change this file? (:meth:`RepositoryGraph.impact`)
- What is the shortest dependency path between two files?
  (:meth:`RepositoryGraph.shortest_path`)
- Which import cycles exist? (:meth:`RepositoryGraph.detect_cycles`)
- What classes inherit from this base?
  (:meth:`RepositoryGraph.neighbors_in` with ``EdgeKind.INHERITS``)

Node kinds
----------
* :attr:`NodeKind.FILE`   — one node per indexed file (id = relative path)
* :attr:`NodeKind.SYMBOL` — one node per symbol (id = ``"{path}#{qn}"``)

Edge kinds
----------
* :attr:`EdgeKind.IMPORTS`  — FILE → FILE (file imports another file)
* :attr:`EdgeKind.DEFINES`  — FILE → SYMBOL (file defines a symbol)
* :attr:`EdgeKind.CONTAINS` — SYMBOL → SYMBOL (parent contains child)
* :attr:`EdgeKind.INHERITS` — SYMBOL → SYMBOL (class → base class)
* :attr:`EdgeKind.CALLS`    — SYMBOL → SYMBOL (reserved for Phase 7)

Usage
-----
::

    from src.repository.graph import RepositoryGraph
    from src.repository.index import RepositoryIndex

    index = RepositoryIndex.build(parse_results)
    graph = RepositoryGraph.build(index)

    cycles = graph.detect_cycles()
    result = graph.impact("src/agent/agent.py")
    path   = graph.shortest_path("src/a.py", "src/b.py")

    print(graph.stats())
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

from src.repository.index import RepositoryIndex
from src.repository.parsers import SymbolKind

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph vocabulary
# ---------------------------------------------------------------------------


class NodeKind(str, Enum):
    """The two vertex types in the repository graph."""

    FILE = "file"
    SYMBOL = "symbol"


class EdgeKind(str, Enum):
    """The five directed relationship types between graph nodes."""

    IMPORTS = "imports"     # FILE → FILE   — file imports another file
    DEFINES = "defines"     # FILE → SYMBOL — file defines a symbol
    CONTAINS = "contains"   # SYMBOL → SYMBOL — parent symbol contains child
    INHERITS = "inherits"   # SYMBOL → SYMBOL — class → base class
    CALLS = "calls"         # SYMBOL → SYMBOL — reserved for Phase 7


# ---------------------------------------------------------------------------
# Node and Edge dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    """An immutable vertex in the repository graph.

    Parameters
    ----------
    id:
        Unique string identifier.  FILE nodes use the relative file path;
        SYMBOL nodes use ``"{relative_path}#{qualified_name}"``.
    kind:
        :class:`NodeKind` — FILE or SYMBOL.
    label:
        Human-readable label.  Equals ``relative_path`` for FILE nodes and
        ``qualified_name`` for SYMBOL nodes.
    language:
        :attr:`~src.repository.models.Language.value` string for FILE nodes;
        ``None`` for SYMBOL nodes.
    size:
        File size in bytes for FILE nodes; ``0`` for SYMBOL nodes.
    symbol_kind:
        :attr:`~src.repository.parsers.SymbolKind.value` for SYMBOL nodes;
        ``None`` for FILE nodes.
    file_path:
        Relative file path for SYMBOL nodes (the file that defines the
        symbol); ``None`` for FILE nodes.
    line_start:
        First source line of a SYMBOL node; ``0`` for FILE nodes.
    line_end:
        Last source line of a SYMBOL node; ``0`` for FILE nodes.
    """

    id: str
    kind: NodeKind
    label: str
    language: str | None = None
    size: int = 0
    symbol_kind: str | None = None
    file_path: str | None = None
    line_start: int = 0
    line_end: int = 0

    def __repr__(self) -> str:
        return f"Node({self.kind.value} {self.id!r})"


@dataclass(frozen=True)
class Edge:
    """An immutable directed, typed relationship between two graph nodes.

    Parameters
    ----------
    source:
        ID of the source node.
    target:
        ID of the target node.
    kind:
        :class:`EdgeKind` — the relationship type.
    imported_names:
        For :attr:`EdgeKind.IMPORTS` edges: the names explicitly imported
        from the target file (e.g. ``("Path", "PurePath")``).  Empty for
        all other edge kinds.
    base_name:
        For :attr:`EdgeKind.INHERITS` edges: the base class name as written
        in the source (e.g. ``"Base"`` or ``"module.Base"``).  Empty for
        all other edge kinds.
    """

    source: str
    target: str
    kind: EdgeKind
    imported_names: tuple[str, ...] = ()
    base_name: str = ""

    def __repr__(self) -> str:
        return f"Edge({self.source!r} --{self.kind.value}--> {self.target!r})"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImpactResult:
    """Impact analysis for a single file change.

    Parameters
    ----------
    changed_file:
        The file whose change was analysed.
    direct_dependents:
        Files that have a direct IMPORTS edge pointing to *changed_file*,
        sorted by relative path.
    transitive_dependents:
        All files that transitively import *changed_file* (BFS over
        reverse IMPORTS edges), sorted by relative path.
    affected_symbol_count:
        Total number of symbols defined in all dependent files combined.
    risk_level:
        ``"LOW"`` (≤ 2 transitive dependents), ``"MEDIUM"`` (3–10),
        or ``"HIGH"`` (> 10).
    """

    changed_file: str
    direct_dependents: list[str]
    transitive_dependents: list[str]
    affected_symbol_count: int
    risk_level: str

    def __repr__(self) -> str:
        return (
            f"ImpactResult({self.changed_file!r}"
            f" direct={len(self.direct_dependents)}"
            f" transitive={len(self.transitive_dependents)}"
            f" risk={self.risk_level})"
        )


@dataclass(frozen=True)
class GraphStats:
    """Statistics for one :class:`RepositoryGraph` build.

    Parameters
    ----------
    node_count:
        Total number of nodes (FILE + SYMBOL).
    edge_count:
        Total number of deduplicated edges.
    file_node_count:
        Number of FILE nodes.
    symbol_node_count:
        Number of SYMBOL nodes.
    imports_edge_count:
        Number of IMPORTS edges.
    defines_edge_count:
        Number of DEFINES edges.
    contains_edge_count:
        Number of CONTAINS edges.
    inherits_edge_count:
        Number of INHERITS edges.
    cycle_count:
        Number of circular import groups (SCCs of size > 1).
    build_duration_ms:
        Wall-clock build time in milliseconds.
    """

    node_count: int
    edge_count: int
    file_node_count: int
    symbol_node_count: int
    imports_edge_count: int
    defines_edge_count: int
    contains_edge_count: int
    inherits_edge_count: int
    cycle_count: int
    build_duration_ms: float

    def __repr__(self) -> str:
        return (
            f"GraphStats(nodes={self.node_count}"
            f" edges={self.edge_count}"
            f" cycles={self.cycle_count}"
            f" build={self.build_duration_ms:.1f}ms)"
        )


# ---------------------------------------------------------------------------
# RepositoryGraph
# ---------------------------------------------------------------------------


class RepositoryGraph:
    """Directed typed graph of file and symbol relationships.

    Build via :meth:`build` — the constructor is not part of the public API.

    Internal storage
    ----------------
    ``_nodes``    — ``dict[id, Node]`` for O(1) node lookup
    ``_adj_out``  — ``dict[id, list[Edge]]`` for forward traversal
    ``_adj_in``   — ``dict[id, list[Edge]]`` for reverse traversal
    ``_edge_set`` — ``set[(source, target, kind)]`` for deduplication

    Both adjacency lists are maintained simultaneously so no transposed
    graph needs to be built on-the-fly for reverse traversals.

    Thread safety
    -------------
    After :meth:`build` returns the graph is read-only.  All query methods
    are safe to call concurrently.  :meth:`build` itself must be called
    once at initialisation time.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._adj_out: dict[str, list[Edge]] = {}
        self._adj_in: dict[str, list[Edge]] = {}
        self._edge_set: set[tuple[str, str, EdgeKind]] = set()
        self._stats: GraphStats | None = None

    @classmethod
    def build(cls, index: RepositoryIndex) -> "RepositoryGraph":
        """Build a ``RepositoryGraph`` from a fully built ``RepositoryIndex``.

        Parameters
        ----------
        index:
            A :class:`~src.repository.index.RepositoryIndex` returned by
            :meth:`~src.repository.index.RepositoryIndex.build`.

        Returns
        -------
        RepositoryGraph
            A fully built, read-only graph ready for queries.

        Performance
        -----------
        Linear in ``nodes + edges``.  For a 5 000-symbol / 200-file repo
        this completes in under 100 ms.
        """
        t0 = time.monotonic()
        g = cls()

        # Step 1: module map — dotted module name → relative file path
        module_map = _build_module_map(index)

        # Step 2: FILE nodes
        for fi in index.indexed_files():
            node = Node(
                id=fi.relative_path,
                kind=NodeKind.FILE,
                label=fi.relative_path,
                language=fi.language.value,
                size=fi.size,
            )
            g._add_node(node)

        # Step 3: SYMBOL nodes + DEFINES + CONTAINS edges
        for fi in index.indexed_files():
            for entry in index.symbols_in_file(fi.relative_path):
                sym = entry.symbol
                node_id = f"{fi.relative_path}#{sym.qualified_name}"
                sym_node = Node(
                    id=node_id,
                    kind=NodeKind.SYMBOL,
                    label=sym.qualified_name,
                    symbol_kind=sym.kind.value,
                    file_path=fi.relative_path,
                    line_start=sym.line_start,
                    line_end=sym.line_end,
                )
                g._add_node(sym_node)

                # DEFINES: file → symbol (top-level and nested)
                g._add_edge(Edge(
                    source=fi.relative_path,
                    target=node_id,
                    kind=EdgeKind.DEFINES,
                ))

                # CONTAINS: parent symbol → this symbol
                if sym.parent is not None:
                    parent_id = f"{fi.relative_path}#{sym.parent}"
                    if parent_id in g._nodes:
                        g._add_edge(Edge(
                            source=parent_id,
                            target=node_id,
                            kind=EdgeKind.CONTAINS,
                        ))

        # Step 4: IMPORTS edges — resolve import strings to file paths
        for fi in index.indexed_files():
            for imp_str in index.imports_for(fi.relative_path):
                parsed = _parse_import_string(imp_str)
                if parsed is None:
                    continue
                n_dots, module_name = parsed
                imported_names = tuple(_extract_imported_names(imp_str))

                if n_dots > 0 and module_name == "":
                    # "from . import X" style — each imported name may be a
                    # submodule of the current package. Resolve each name
                    # to its own file; fall back to the package __init__
                    # only when none of them resolved.
                    #
                    # The fallback used to be unconditional, which added a
                    # second edge to __init__ alongside the real one. True
                    # in the sense that Python runs __init__ on the way
                    # through, but it is not a dependency on anything the
                    # importer uses, and it accumulates: every module in a
                    # package that borrows a sibling pointed one at
                    # __init__, so impact() rated an empty __init__ as
                    # risky as the file everything actually imports. A risk
                    # score that cries wolf on the safest file in the
                    # folder stops being read.
                    #
                    # When nothing named resolves — the name is defined in
                    # __init__ itself — the fallback edge is the only true
                    # one left, so it stays.
                    resolved_any = False

                    for name in imported_names:
                        tp = _resolve_to_file(n_dots, name, fi.relative_path, module_map)
                        if tp is not None and tp in g._nodes and tp != fi.relative_path:
                            g._add_edge(Edge(
                                source=fi.relative_path,
                                target=tp,
                                kind=EdgeKind.IMPORTS,
                                imported_names=(name,),
                            ))
                            resolved_any = True

                    tp = (
                        None
                        if resolved_any
                        else _resolve_to_file(n_dots, "", fi.relative_path, module_map)
                    )
                    if tp is not None and tp in g._nodes and tp != fi.relative_path:
                        g._add_edge(Edge(
                            source=fi.relative_path,
                            target=tp,
                            kind=EdgeKind.IMPORTS,
                            imported_names=imported_names,
                        ))
                    continue

                target_path = _resolve_to_file(
                    n_dots, module_name, fi.relative_path, module_map
                )
                if target_path is not None and target_path in g._nodes:
                    # Avoid self-import edges (can happen with __init__.py)
                    if target_path == fi.relative_path:
                        continue
                    g._add_edge(Edge(
                        source=fi.relative_path,
                        target=target_path,
                        kind=EdgeKind.IMPORTS,
                        imported_names=imported_names,
                    ))

        # Step 5: INHERITS edges — resolve base class names to SYMBOL nodes
        for fi in index.indexed_files():
            for entry in index.symbols_in_file(fi.relative_path):
                sym = entry.symbol
                if sym.kind is not SymbolKind.CLASS:
                    continue
                base_classes: list[str] = getattr(sym, "base_classes", [])
                if not base_classes:
                    continue
                class_node_id = f"{fi.relative_path}#{sym.qualified_name}"
                for base_name in base_classes:
                    target_id = _find_symbol_node(base_name, class_node_id, g._nodes)
                    if target_id is not None:
                        g._add_edge(Edge(
                            source=class_node_id,
                            target=target_id,
                            kind=EdgeKind.INHERITS,
                            base_name=base_name,
                        ))

        # Step 6: compute cycle count
        cycles = g.detect_cycles()

        elapsed_ms = (time.monotonic() - t0) * 1000

        g._stats = GraphStats(
            node_count=len(g._nodes),
            edge_count=len(g._edge_set),
            file_node_count=sum(1 for n in g._nodes.values() if n.kind is NodeKind.FILE),
            symbol_node_count=sum(1 for n in g._nodes.values() if n.kind is NodeKind.SYMBOL),
            imports_edge_count=sum(1 for _, _, k in g._edge_set if k is EdgeKind.IMPORTS),
            defines_edge_count=sum(1 for _, _, k in g._edge_set if k is EdgeKind.DEFINES),
            contains_edge_count=sum(1 for _, _, k in g._edge_set if k is EdgeKind.CONTAINS),
            inherits_edge_count=sum(1 for _, _, k in g._edge_set if k is EdgeKind.INHERITS),
            cycle_count=len(cycles),
            build_duration_ms=elapsed_ms,
        )

        logger.debug("Graph built: %s", g._stats)
        return g

    def _add_node(self, node: Node) -> None:
        self._nodes[node.id] = node
        self._adj_out.setdefault(node.id, [])
        self._adj_in.setdefault(node.id, [])

    def _add_edge(self, edge: Edge) -> None:
        key = (edge.source, edge.target, edge.kind)
        if key in self._edge_set:
            return
        self._edge_set.add(key)
        self._adj_out.setdefault(edge.source, []).append(edge)
        self._adj_in.setdefault(edge.target, []).append(edge)

    # ------------------------------------------------------------------
    # Node and edge queries
    # ------------------------------------------------------------------

    def node(self, node_id: str) -> Node | None:
        """Return the node with *node_id*, or ``None`` if not present."""
        return self._nodes.get(node_id)

    def nodes(self, kind: NodeKind | None = None) -> list[Node]:
        """Return all nodes, optionally filtered by *kind*."""
        if kind is None:
            return list(self._nodes.values())
        return [n for n in self._nodes.values() if n.kind is kind]

    def edges(self, kind: EdgeKind | None = None) -> list[Edge]:
        """Return all edges, optionally filtered by *kind*.

        Each edge is returned exactly once.
        """
        seen: set[tuple[str, str, EdgeKind]] = set()
        result: list[Edge] = []
        for edge_list in self._adj_out.values():
            for e in edge_list:
                key = (e.source, e.target, e.kind)
                if key not in seen:
                    if kind is None or e.kind is kind:
                        result.append(e)
                    seen.add(key)
        return result

    def edges_out(self, node_id: str, kind: EdgeKind | None = None) -> list[Edge]:
        """Return outgoing edges from *node_id*, optionally filtered by *kind*."""
        return [
            e for e in self._adj_out.get(node_id, [])
            if kind is None or e.kind is kind
        ]

    def edges_in(self, node_id: str, kind: EdgeKind | None = None) -> list[Edge]:
        """Return incoming edges to *node_id*, optionally filtered by *kind*."""
        return [
            e for e in self._adj_in.get(node_id, [])
            if kind is None or e.kind is kind
        ]

    def neighbors_out(self, node_id: str, kind: EdgeKind | None = None) -> list[Node]:
        """Return nodes reachable via one outgoing edge from *node_id*.

        Parameters
        ----------
        node_id:
            Source node ID.
        kind:
            If given, only follow edges of this kind.
        """
        targets = []
        for e in self._adj_out.get(node_id, []):
            if kind is None or e.kind is kind:
                n = self._nodes.get(e.target)
                if n is not None:
                    targets.append(n)
        return targets

    def neighbors_in(self, node_id: str, kind: EdgeKind | None = None) -> list[Node]:
        """Return nodes that have an outgoing edge pointing to *node_id*.

        Parameters
        ----------
        node_id:
            Target node ID.
        kind:
            If given, only follow edges of this kind.
        """
        sources = []
        for e in self._adj_in.get(node_id, []):
            if kind is None or e.kind is kind:
                n = self._nodes.get(e.source)
                if n is not None:
                    sources.append(n)
        return sources

    # ------------------------------------------------------------------
    # Graph algorithms
    # ------------------------------------------------------------------

    def detect_cycles(self) -> list[list[str]]:
        """Return circular import groups using Tarjan's SCC algorithm.

        Only the IMPORTS subgraph (file nodes) is analysed.  Returned
        lists contain relative file paths, sorted alphabetically.

        Returns
        -------
        list[list[str]]
            One list per circular import group.  Empty if the import
            graph is acyclic.

        Algorithm
        ---------
        Iterative Tarjan's SCC — no Python recursion limit concerns.
        """
        file_ids = [n.id for n in self._nodes.values() if n.kind is NodeKind.FILE]

        index_counter = 0
        index_map: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        on_stack: set[str] = set()
        scc_stack: list[str] = []
        sccs: list[list[str]] = []

        def _import_targets(node_id: str) -> list[str]:
            return [
                e.target
                for e in self._adj_out.get(node_id, [])
                if e.kind is EdgeKind.IMPORTS and e.target in self._nodes
            ]

        for start in file_ids:
            if start in index_map:
                continue

            index_map[start] = index_counter
            lowlink[start] = index_counter
            index_counter += 1
            scc_stack.append(start)
            on_stack.add(start)

            # work_stack: [node_id, edge_index, neighbors_list]
            work: list[list] = [[start, 0, _import_targets(start)]]

            while work:
                frame = work[-1]
                v: str = frame[0]
                ei: int = frame[1]
                neighbors: list[str] = frame[2]

                if ei < len(neighbors):
                    w = neighbors[ei]
                    frame[1] += 1

                    if w not in index_map:
                        index_map[w] = index_counter
                        lowlink[w] = index_counter
                        index_counter += 1
                        scc_stack.append(w)
                        on_stack.add(w)
                        work.append([w, 0, _import_targets(w)])
                    elif w in on_stack:
                        lowlink[v] = min(lowlink[v], index_map[w])
                else:
                    # All neighbours processed — pop and propagate lowlink
                    work.pop()
                    if work:
                        parent: str = work[-1][0]
                        lowlink[parent] = min(lowlink[parent], lowlink[v])

                    # Check if v is the root of an SCC
                    if lowlink[v] == index_map[v]:
                        scc: list[str] = []
                        while True:
                            w = scc_stack.pop()
                            on_stack.discard(w)
                            scc.append(w)
                            if w == v:
                                break
                        if len(scc) > 1:
                            sccs.append(sorted(scc))

        return sccs

    def shortest_path(
        self,
        source: str,
        target: str,
        kind: EdgeKind | None = None,
    ) -> list[str] | None:
        """Return the shortest path of node IDs from *source* to *target*.

        Uses BFS.  Only edges of the given *kind* are followed when *kind*
        is not ``None``.

        Parameters
        ----------
        source:
            Starting node ID.
        target:
            Destination node ID.
        kind:
            Edge kind to restrict traversal to, or ``None`` for all edges.

        Returns
        -------
        list[str] | None
            Ordered list of node IDs from *source* to *target* (inclusive),
            or ``None`` if no path exists.
        """
        if source not in self._nodes or target not in self._nodes:
            return None
        if source == target:
            return [source]

        from collections import deque

        visited: set[str] = {source}
        queue: deque[list[str]] = deque([[source]])

        while queue:
            path = queue.popleft()
            current = path[-1]
            for e in self._adj_out.get(current, []):
                if kind is not None and e.kind is not kind:
                    continue
                nxt = e.target
                if nxt not in self._nodes:
                    continue
                if nxt == target:
                    return path + [nxt]
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(path + [nxt])

        return None

    def transitive_dependencies(self, file_path: str) -> list[str]:
        """Return all files that *file_path* transitively imports.

        Follows IMPORTS edges in the forward direction.  The *file_path*
        itself is not included.  Result is sorted alphabetically.

        Parameters
        ----------
        file_path:
            Repository-relative file path (a FILE node id).
        """
        if file_path not in self._nodes:
            return []

        from collections import deque

        visited: set[str] = set()
        queue: deque[str] = deque([file_path])

        while queue:
            current = queue.popleft()
            for e in self._adj_out.get(current, []):
                if e.kind is not EdgeKind.IMPORTS:
                    continue
                if e.target not in visited and e.target != file_path:
                    visited.add(e.target)
                    queue.append(e.target)

        return sorted(visited)

    def transitive_dependents(self, file_path: str) -> list[str]:
        """Return all files that transitively import *file_path*.

        Follows IMPORTS edges in the reverse direction.  The *file_path*
        itself is not included.  Result is sorted alphabetically.

        Parameters
        ----------
        file_path:
            Repository-relative file path (a FILE node id).
        """
        if file_path not in self._nodes:
            return []

        from collections import deque

        visited: set[str] = set()
        queue: deque[str] = deque([file_path])

        while queue:
            current = queue.popleft()
            for e in self._adj_in.get(current, []):
                if e.kind is not EdgeKind.IMPORTS:
                    continue
                if e.source not in visited and e.source != file_path:
                    visited.add(e.source)
                    queue.append(e.source)

        return sorted(visited)

    def impact(self, file_path: str) -> ImpactResult:
        """Compute the impact of changing *file_path*.

        Parameters
        ----------
        file_path:
            Repository-relative file path of the file being changed.

        Returns
        -------
        ImpactResult
            Contains direct dependents, transitive dependents, affected
            symbol count, and risk classification.
        """
        direct = sorted(
            e.source
            for e in self._adj_in.get(file_path, [])
            if e.kind is EdgeKind.IMPORTS
        )

        transitive = self.transitive_dependents(file_path)

        # Count symbols defined in all dependent files
        all_dependents = set(direct) | set(transitive)
        affected_symbols = sum(
            1
            for dep in all_dependents
            for e in self._adj_out.get(dep, [])
            if e.kind is EdgeKind.DEFINES
        )

        n = len(transitive)
        if n <= 2:
            risk = "LOW"
        elif n <= 10:
            risk = "MEDIUM"
        else:
            risk = "HIGH"

        return ImpactResult(
            changed_file=file_path,
            direct_dependents=direct,
            transitive_dependents=transitive,
            affected_symbol_count=affected_symbols,
            risk_level=risk,
        )

    # ------------------------------------------------------------------
    # PageRank
    # ------------------------------------------------------------------

    def pagerank(
        self,
        personalization: dict[str, float] | None = None,
        alpha: float = 0.85,
    ) -> dict[str, float]:
        """
        Compute PageRank scores for all FILE nodes in the import graph.

        Uses NetworkX under the hood. The base DiGraph is built and
        cached on the first call; subsequent calls only re-run the
        PageRank solver (which varies with `personalization`).

        Parameters
        ----------
        personalization:
            ``{file_id: weight}`` bias map. Files not present get the
            baseline weight (1.0). Higher-weight files attract more
            random-teleportation probability, biasing the stationary
            distribution toward them and their importers.
            Pass ``None`` for an unbiased run.
        alpha:
            Damping factor (default 0.85 — the standard value).

        Returns
        -------
        dict[str, float]
            File ID → normalized PageRank score. Only FILE nodes are
            included. Files absent from the graph return 0.0 implicitly.
        """
        try:
            import networkx as nx
        except ImportError:
            # NetworkX is not available — return equal weights so callers
            # degrade gracefully to term-match-only ranking.
            file_nodes = self.nodes(kind=NodeKind.FILE)
            n = len(file_nodes)
            score = 1.0 / n if n else 0.0
            return {node.id: score for node in file_nodes}

        # Build or reuse the cached base graph (FILE nodes + IMPORTS edges).
        if not hasattr(self, "_nx_import_graph"):
            g: nx.DiGraph = nx.DiGraph()
            for node in self.nodes(kind=NodeKind.FILE):
                g.add_node(node.id)
            for edge in self.edges(kind=EdgeKind.IMPORTS):
                g.add_edge(edge.source, edge.target)
            # ponytail: caches on the instance — safe because the graph
            # is read-only after build().
            object.__setattr__(self, "_nx_import_graph", g)  # type: ignore[call-arg]

        g = self._nx_import_graph  # type: ignore[attr-defined]

        if g.number_of_nodes() == 0:
            return {}

        nx_personalization: dict[str, float] | None = None
        if personalization:
            # Normalise so all weights sum to 1.0 (nx.pagerank requirement).
            baseline = 1.0
            weighted = {
                nid: personalization.get(nid, baseline) for nid in g.nodes()
            }
            total = sum(weighted.values())
            if total > 0:
                nx_personalization = {k: v / total for k, v in weighted.items()}

        try:
            # Use the pure-Python power-iteration implementation so that
            # scipy is not required (nx.pagerank dispatches to scipy when
            # it is installed, raising ModuleNotFoundError otherwise).
            from networkx.algorithms.link_analysis.pagerank_alg import (
                _pagerank_python,
            )
            scores: dict[str, float] = _pagerank_python(
                g,
                alpha=alpha,
                personalization=nx_personalization,
                max_iter=100,
                tol=1.0e-6,
            )
        except nx.PowerIterationFailedConvergence:
            logger.warning("PageRank did not converge; falling back to equal weights.")
            n = g.number_of_nodes()
            scores = {nid: 1.0 / n for nid in g.nodes()}

        return scores

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> GraphStats:
        """Return build statistics.

        :raises RuntimeError: if called before :meth:`build`.
        """
        if self._stats is None:
            raise RuntimeError("RepositoryGraph.stats() called before build()")
        return self._stats

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return the total number of nodes."""
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        """Return ``True`` if *node_id* is a node in this graph."""
        return node_id in self._nodes

    def __repr__(self) -> str:
        s = self._stats
        if s is None:
            return "RepositoryGraph(unbuilt)"
        return (
            f"RepositoryGraph(nodes={s.node_count}"
            f" edges={s.edge_count}"
            f" cycles={s.cycle_count})"
        )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "EdgeKind",
    "Edge",
    "GraphStats",
    "ImpactResult",
    "NodeKind",
    "Node",
    "RepositoryGraph",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_module_map(index: RepositoryIndex) -> dict[str, str]:
    """Build a mapping from dotted module name to relative file path.

    Examples::

        "src.repository.index"   → "src/repository/index.py"
        "src.repository.parsers" → "src/repository/parsers/__init__.py"
        "src.repository"         → "src/repository/__init__.py"
    """
    module_map: dict[str, str] = {}
    for fi in index.indexed_files():
        rp = fi.relative_path
        parts = rp.replace("\\", "/").split("/")
        filename = parts[-1]
        if filename == "__init__.py":
            # Package: directory becomes the module key
            key = ".".join(parts[:-1]) if len(parts) > 1 else ""
        else:
            # Module: strip known source extension, join directory + stem
            stem = filename
            for ext in (".py", ".pyi", ".pyx", ".js", ".ts", ".tsx", ".mjs", ".cjs"):
                if stem.endswith(ext):
                    stem = stem[: -len(ext)]
                    break
            key = ".".join(parts[:-1] + [stem]) if len(parts) > 1 else stem
        if key:
            module_map[key] = rp
    return module_map


def _parse_import_string(imp_str: str) -> tuple[int, str] | None:
    """Parse an import string into ``(n_dots, module_name)``.

    Returns ``None`` for unrecognised strings.
    ``n_dots == 0`` means an absolute import.

    Examples::

        "import os"                  → (0, "os")
        "from pathlib import Path"   → (0, "pathlib")
        "from . import models"       → (1, "")
        "from ..utils import helper" → (2, "utils")
    """
    imp_str = imp_str.strip()
    if imp_str.startswith("from "):
        rest = imp_str[5:]
        import_idx = rest.find(" import ")
        if import_idx == -1:
            return None
        module_part = rest[:import_idx].strip()
        n_dots = 0
        while n_dots < len(module_part) and module_part[n_dots] == ".":
            n_dots += 1
        module_name = module_part[n_dots:]
        return (n_dots, module_name)
    if imp_str.startswith("import "):
        rest = imp_str[7:].strip()
        # Take the first module name (before comma or " as ")
        module_name = rest.split(",")[0].split(" as ")[0].strip()
        return (0, module_name)
    return None


def _resolve_to_file(
    n_dots: int,
    module_name: str,
    current_file: str,
    module_map: dict[str, str],
) -> str | None:
    """Resolve ``(n_dots, module_name)`` to a relative file path.

    Returns ``None`` for stdlib/third-party imports or unresolvable
    relative imports.

    Parameters
    ----------
    n_dots:
        Number of leading dots.  ``0`` means absolute.
    module_name:
        Module name (may contain dots).  May be empty for
        ``"from . import X"``.
    current_file:
        Relative path of the importing file.
    module_map:
        Dotted-name → relative-path mapping from :func:`_build_module_map`.
    """
    if n_dots == 0:
        # Absolute import — direct lookup only
        return module_map.get(module_name)

    # Relative import — compute the current package directory
    parts = current_file.replace("\\", "/").split("/")
    pkg_parts = parts[:-1]  # strip filename; this is the current package

    up = n_dots - 1
    if up > len(pkg_parts):
        return None  # too many dots
    base_parts = pkg_parts[: len(pkg_parts) - up] if up > 0 else pkg_parts

    if module_name:
        target_key = ".".join(base_parts + module_name.split("."))
    else:
        target_key = ".".join(base_parts)

    return module_map.get(target_key)


def _extract_imported_names(imp_str: str) -> list[str]:
    """Extract the names explicitly imported by an import string.

    Returns an empty list for ``"import X"`` style imports.

    Examples::

        "from pathlib import Path"          → ["Path"]
        "from pathlib import Path, PurePath" → ["Path", "PurePath"]
        "from . import models"              → ["models"]
        "import os"                         → []
    """
    if " import " not in imp_str:
        return []
    after = imp_str.split(" import ", 1)[1].strip().strip("()")
    names = [n.strip().split(" as ")[0].strip() for n in after.split(",")]
    return [n for n in names if n]


def _find_symbol_node(
    base_name: str,
    exclude_id: str,
    nodes: dict[str, Node],
) -> str | None:
    """Find the best-matching SYMBOL node for a base class name.

    Matching priority (first match wins):
    1. Exact label match (``label == base_name``).
    2. Label ends with ``.{base_name}`` (suffix for nested qualified names).
    3. Label equals the last component of *base_name* (simple name from
       a dotted base like ``"module.Base"``).

    Parameters
    ----------
    base_name:
        Base class name as extracted from source (e.g. ``"Base"`` or
        ``"module.Base"``).
    exclude_id:
        The class node's own id — prevents self-inheritance edges.
    nodes:
        The graph's ``_nodes`` dict.
    """
    last = base_name.split(".")[-1]

    # Pass 1: exact label match
    for nid, node in nodes.items():
        if node.kind is NodeKind.SYMBOL and nid != exclude_id:
            if node.label == base_name:
                return nid

    # Pass 2: suffix match or last-component match
    for nid, node in nodes.items():
        if node.kind is NodeKind.SYMBOL and nid != exclude_id:
            if node.label.endswith("." + base_name) or node.label == last:
                return nid

    return None
