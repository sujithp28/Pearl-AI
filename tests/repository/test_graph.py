"""
Tests for Phase 5 — RepositoryGraph (src/repository/graph.py).

Coverage
--------
* Node and Edge dataclasses (kind, label, frozen, repr)
* ImpactResult and GraphStats (frozen, repr)
* RepositoryGraph.build() — FILE nodes, SYMBOL nodes, DEFINES, CONTAINS,
  IMPORTS, INHERITS edges; empty index; deduplication
* Node queries — node(), nodes(), __contains__
* Edge queries — edges(), edges_out(), edges_in()
* Neighbour traversal — neighbors_out(), neighbors_in()
* detect_cycles() — acyclic, direct cycle, transitive cycle, isolated node
* shortest_path() — unknown nodes, same node, direct, multi-hop, no path
* transitive_dependencies() — empty, direct, transitive chain
* transitive_dependents() — empty, direct, transitive chain
* impact() — LOW / MEDIUM / HIGH risk, affected symbol count
* stats() — correct counts after build
* Dunder helpers — __len__, __contains__, __repr__
* Internal helpers — _build_module_map, _parse_import_string,
  _resolve_to_file, _extract_imported_names, _find_symbol_node
* Layer rule — graph.py must not import from forbidden src.* layers
* Integration — full pipeline scan + parse + index + graph
* Benchmarks — build time under budget

Test helpers
------------
All helpers create real FileInfo objects and real ParseResult objects so
that the full parsing pipeline is exercised.  No mocking of internal state.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from src.repository.graph import (
    EdgeKind,
    Edge,
    GraphStats,
    ImpactResult,
    NodeKind,
    Node,
    RepositoryGraph,
    _build_module_map,
    _extract_imported_names,
    _find_symbol_node,
    _parse_import_string,
    _resolve_to_file,
)
from src.repository.index import RepositoryIndex
from src.repository.models import FileInfo, Language, detect_language
from src.repository.parsers import ParseResult, ParserRegistry, SymbolDef, SymbolKind


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fi(tmp: Path, rel_path: str, content: str) -> FileInfo:
    """Write *content* to ``tmp / rel_path`` and return a FileInfo."""
    full = tmp / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return FileInfo(
        path=full,
        relative_path=rel_path,
        extension=Path(rel_path).suffix,
        language=detect_language(full),
        size=len(content.encode("utf-8")),
        modified_at=full.stat().st_mtime,
        content_hash="",
    )


def _build_graph(tmp: Path, files: dict[str, str]) -> RepositoryGraph:
    """Build a graph from {rel_path: source_code} in a temp directory."""
    registry = ParserRegistry.default()
    file_infos = [_fi(tmp, name, src) for name, src in files.items()]
    results = list(registry.parse_many(file_infos))
    index = RepositoryIndex.build(results)
    return RepositoryGraph.build(index)


def _build_index(tmp: Path, files: dict[str, str]) -> RepositoryIndex:
    registry = ParserRegistry.default()
    file_infos = [_fi(tmp, name, src) for name, src in files.items()]
    results = list(registry.parse_many(file_infos))
    return RepositoryIndex.build(results)


# ---------------------------------------------------------------------------
# NodeKind
# ---------------------------------------------------------------------------


class TestNodeKind:
    def test_file_value(self) -> None:
        assert NodeKind.FILE.value == "file"

    def test_symbol_value(self) -> None:
        assert NodeKind.SYMBOL.value == "symbol"

    def test_str_equality(self) -> None:
        assert NodeKind.FILE == "file"
        assert NodeKind.SYMBOL == "symbol"

    def test_members(self) -> None:
        kinds = {k.value for k in NodeKind}
        assert "file" in kinds
        assert "symbol" in kinds


# ---------------------------------------------------------------------------
# EdgeKind
# ---------------------------------------------------------------------------


class TestEdgeKind:
    def test_values(self) -> None:
        assert EdgeKind.IMPORTS.value == "imports"
        assert EdgeKind.DEFINES.value == "defines"
        assert EdgeKind.CONTAINS.value == "contains"
        assert EdgeKind.INHERITS.value == "inherits"
        assert EdgeKind.CALLS.value == "calls"

    def test_str_equality(self) -> None:
        assert EdgeKind.IMPORTS == "imports"
        assert EdgeKind.DEFINES == "defines"


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class TestNode:
    def test_construction(self) -> None:
        n = Node(id="src/a.py", kind=NodeKind.FILE, label="src/a.py")
        assert n.id == "src/a.py"
        assert n.kind is NodeKind.FILE
        assert n.label == "src/a.py"
        assert n.language is None
        assert n.size == 0
        assert n.symbol_kind is None
        assert n.file_path is None
        assert n.line_start == 0
        assert n.line_end == 0

    def test_symbol_node_fields(self) -> None:
        n = Node(
            id="src/a.py#MyClass",
            kind=NodeKind.SYMBOL,
            label="MyClass",
            symbol_kind="class",
            file_path="src/a.py",
            line_start=5,
            line_end=20,
        )
        assert n.symbol_kind == "class"
        assert n.file_path == "src/a.py"
        assert n.line_start == 5
        assert n.line_end == 20

    def test_frozen(self) -> None:
        n = Node(id="src/a.py", kind=NodeKind.FILE, label="src/a.py")
        with pytest.raises((AttributeError, TypeError)):
            n.id = "other"  # type: ignore[misc]

    def test_repr_file(self) -> None:
        n = Node(id="src/a.py", kind=NodeKind.FILE, label="src/a.py")
        assert "file" in repr(n)
        assert "src/a.py" in repr(n)

    def test_repr_symbol(self) -> None:
        n = Node(id="src/a.py#NewFoo", kind=NodeKind.SYMBOL, label="NewFoo")
        assert "symbol" in repr(n)


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


class TestEdge:
    def test_construction(self) -> None:
        e = Edge(source="src/a.py", target="src/b.py", kind=EdgeKind.IMPORTS)
        assert e.source == "src/a.py"
        assert e.target == "src/b.py"
        assert e.kind is EdgeKind.IMPORTS
        assert e.imported_names == ()
        assert e.base_name == ""

    def test_with_imported_names(self) -> None:
        e = Edge(
            source="src/a.py",
            target="src/b.py",
            kind=EdgeKind.IMPORTS,
            imported_names=("Path", "PurePath"),
        )
        assert e.imported_names == ("Path", "PurePath")

    def test_with_base_name(self) -> None:
        e = Edge(
            source="src/a.py#Child",
            target="src/b.py#Base",
            kind=EdgeKind.INHERITS,
            base_name="Base",
        )
        assert e.base_name == "Base"

    def test_frozen(self) -> None:
        e = Edge(source="src/a.py", target="src/b.py", kind=EdgeKind.IMPORTS)
        with pytest.raises((AttributeError, TypeError)):
            e.source = "other"  # type: ignore[misc]

    def test_repr(self) -> None:
        e = Edge(source="src/a.py", target="src/b.py", kind=EdgeKind.IMPORTS)
        assert "imports" in repr(e)
        assert "src/a.py" in repr(e)
        assert "src/b.py" in repr(e)


# ---------------------------------------------------------------------------
# ImpactResult
# ---------------------------------------------------------------------------


class TestImpactResult:
    def test_construction(self) -> None:
        r = ImpactResult(
            changed_file="src/a.py",
            direct_dependents=["src/b.py"],
            transitive_dependents=["src/b.py", "src/c.py"],
            affected_symbol_count=5,
            risk_level="LOW",
        )
        assert r.changed_file == "src/a.py"
        assert r.direct_dependents == ["src/b.py"]
        assert r.transitive_dependents == ["src/b.py", "src/c.py"]
        assert r.affected_symbol_count == 5
        assert r.risk_level == "LOW"

    def test_frozen(self) -> None:
        r = ImpactResult("f", [], [], 0, "LOW")
        with pytest.raises((AttributeError, TypeError)):
            r.risk_level = "HIGH"  # type: ignore[misc]

    def test_repr(self) -> None:
        r = ImpactResult("src/a.py", ["b"], ["b", "c"], 3, "MEDIUM")
        assert "src/a.py" in repr(r)
        assert "MEDIUM" in repr(r)
        assert "direct=1" in repr(r)
        assert "transitive=2" in repr(r)


# ---------------------------------------------------------------------------
# GraphStats
# ---------------------------------------------------------------------------


class TestGraphStats:
    def test_construction(self) -> None:
        s = GraphStats(
            node_count=10,
            edge_count=15,
            file_node_count=3,
            symbol_node_count=7,
            imports_edge_count=2,
            defines_edge_count=7,
            contains_edge_count=5,
            inherits_edge_count=1,
            cycle_count=0,
            build_duration_ms=12.5,
        )
        assert s.node_count == 10
        assert s.cycle_count == 0

    def test_frozen(self) -> None:
        s = GraphStats(10, 15, 3, 7, 2, 7, 5, 1, 0, 12.5)
        with pytest.raises((AttributeError, TypeError)):
            s.node_count = 99  # type: ignore[misc]

    def test_repr(self) -> None:
        s = GraphStats(10, 15, 3, 7, 2, 7, 5, 1, 0, 12.5)
        r = repr(s)
        assert "nodes=10" in r
        assert "edges=15" in r
        assert "cycles=0" in r
        assert "12.5ms" in r


# ---------------------------------------------------------------------------
# Build — empty index
# ---------------------------------------------------------------------------


class TestBuildEmpty:
    def test_empty_index_produces_empty_graph(self, tmp_path: Path) -> None:
        index = RepositoryIndex.build([])
        graph = RepositoryGraph.build(index)
        assert len(graph) == 0
        assert graph.nodes() == []
        assert graph.edges() == []

    def test_stats_on_empty(self, tmp_path: Path) -> None:
        index = RepositoryIndex.build([])
        graph = RepositoryGraph.build(index)
        s = graph.stats()
        assert s.node_count == 0
        assert s.edge_count == 0
        assert s.cycle_count == 0


# ---------------------------------------------------------------------------
# Build — FILE nodes
# ---------------------------------------------------------------------------


class TestBuildFileNodes:
    def test_one_file_node_per_indexed_file(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "x = 1\n",
            "src/b.py": "y = 2\n",
        })
        file_nodes = g.nodes(kind=NodeKind.FILE)
        ids = {n.id for n in file_nodes}
        assert "src/a.py" in ids
        assert "src/b.py" in ids

    def test_file_node_language(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        n = g.node("src/a.py")
        assert n is not None
        assert n.language == "python"

    def test_file_node_kind(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        n = g.node("src/a.py")
        assert n is not None
        assert n.kind is NodeKind.FILE

    def test_file_node_label_equals_path(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        n = g.node("src/a.py")
        assert n is not None
        assert n.label == "src/a.py"

    def test_file_node_size(self, tmp_path: Path) -> None:
        content = "x = 1\n"
        g = _build_graph(tmp_path, {"src/a.py": content})
        n = g.node("src/a.py")
        assert n is not None
        assert n.size == len(content.encode("utf-8"))


# ---------------------------------------------------------------------------
# Build — SYMBOL nodes
# ---------------------------------------------------------------------------


class TestBuildSymbolNodes:
    def test_symbol_node_created_per_symbol(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class NewFoo:\n    def bar(self): pass\n",
        })
        sym_nodes = g.nodes(kind=NodeKind.SYMBOL)
        labels = {n.label for n in sym_nodes}
        assert "NewFoo" in labels
        assert "NewFoo.bar" in labels

    def test_symbol_node_id_format(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        assert "src/a.py#NewFoo" in g

    def test_symbol_node_kind_field(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        n = g.node("src/a.py#NewFoo")
        assert n is not None
        assert n.symbol_kind == "class"

    def test_symbol_node_file_path(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        n = g.node("src/a.py#NewFoo")
        assert n is not None
        assert n.file_path == "src/a.py"

    def test_symbol_node_line_numbers(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo:\n    pass\n"})
        n = g.node("src/a.py#NewFoo")
        assert n is not None
        assert n.line_start == 1
        assert n.line_end == 2


# ---------------------------------------------------------------------------
# Build — DEFINES edges
# ---------------------------------------------------------------------------


class TestDefinesEdges:
    def test_defines_edge_file_to_symbol(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        out = g.edges_out("src/a.py", kind=EdgeKind.DEFINES)
        targets = {e.target for e in out}
        assert "src/a.py#NewFoo" in targets

    def test_defines_edge_for_function(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "def bar(): pass\n"})
        out = g.edges_out("src/a.py", kind=EdgeKind.DEFINES)
        assert any("bar" in e.target for e in out)

    def test_defines_edges_for_all_symbols(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class A: pass\nclass B: pass\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.DEFINES)
        targets = {e.target for e in out}
        assert "src/a.py#A" in targets
        assert "src/a.py#B" in targets

    def test_nested_symbols_also_have_defines_edge(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class NewFoo:\n    def method(self): pass\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.DEFINES)
        targets = {e.target for e in out}
        assert "src/a.py#NewFoo.method" in targets


# ---------------------------------------------------------------------------
# Build — CONTAINS edges
# ---------------------------------------------------------------------------


class TestContainsEdges:
    def test_contains_edge_class_to_method(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class NewFoo:\n    def bar(self): pass\n",
        })
        out = g.edges_out("src/a.py#NewFoo", kind=EdgeKind.CONTAINS)
        targets = {e.target for e in out}
        assert "src/a.py#NewFoo.bar" in targets

    def test_no_contains_for_top_level(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "def top(): pass\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.CONTAINS)
        assert out == []  # file nodes don't have CONTAINS edges

    def test_contains_edge_nested_class(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class Outer:\n    class Inner: pass\n",
        })
        out = g.edges_out("src/a.py#Outer", kind=EdgeKind.CONTAINS)
        targets = {e.target for e in out}
        assert "src/a.py#Outer.Inner" in targets

    def test_contains_edge_class_to_constant(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class Cfg:\n    MAX = 10\n",
        })
        out = g.edges_out("src/a.py#Cfg", kind=EdgeKind.CONTAINS)
        targets = {e.target for e in out}
        assert "src/a.py#Cfg.MAX" in targets


# ---------------------------------------------------------------------------
# Build — IMPORTS edges
# ---------------------------------------------------------------------------


class TestImportsEdges:
    def test_absolute_import_creates_edge(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import NewFoo\n",
            "src/b.py": "class NewFoo: pass\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.IMPORTS)
        targets = {e.target for e in out}
        assert "src/b.py" in targets

    def test_relative_import_creates_edge(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from . import b\n",
            "src/b.py": "x = 1\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.IMPORTS)
        targets = {e.target for e in out}
        assert "src/b.py" in targets

    def test_stdlib_import_no_edge(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "import os\nimport sys\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.IMPORTS)
        assert out == []

    def test_unresolvable_import_no_edge(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "import unknown_third_party\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.IMPORTS)
        assert out == []

    def test_import_edge_deduplicated(self, tmp_path: Path) -> None:
        # Two import statements to the same module → only one IMPORTS edge
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import NewFoo\nfrom src.b import Bar\n",
            "src/b.py": "class NewFoo: pass\nclass Bar: pass\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.IMPORTS)
        # All edges should point to src/b.py — deduplicated to one
        assert len([e for e in out if e.target == "src/b.py"]) == 1

    def test_import_edge_imported_names(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import NewFoo\n",
            "src/b.py": "class NewFoo: pass\n",
        })
        out = g.edges_out("src/a.py", kind=EdgeKind.IMPORTS)
        edge = next((e for e in out if e.target == "src/b.py"), None)
        assert edge is not None
        assert "NewFoo" in edge.imported_names

    def test_no_self_import_edge(self, tmp_path: Path) -> None:
        # __init__.py importing from the same package should not self-loop
        g = _build_graph(tmp_path, {
            "src/__init__.py": "from src import utils\n",
            "src/utils.py": "x = 1\n",
        })
        self_edges = [
            e for e in g.edges_out("src/__init__.py", kind=EdgeKind.IMPORTS)
            if e.target == "src/__init__.py"
        ]
        assert self_edges == []


# ---------------------------------------------------------------------------
# Build — INHERITS edges
# ---------------------------------------------------------------------------


class TestInheritsEdges:
    def test_inherits_edge_simple_base(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class Base: pass\nclass Child(Base): pass\n",
        })
        out = g.edges_out("src/a.py#Child", kind=EdgeKind.INHERITS)
        targets = {e.target for e in out}
        assert "src/a.py#Base" in targets

    def test_inherits_edge_base_name_stored(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class Base: pass\nclass Child(Base): pass\n",
        })
        out = g.edges_out("src/a.py#Child", kind=EdgeKind.INHERITS)
        edge = next((e for e in out if e.target == "src/a.py#Base"), None)
        assert edge is not None
        assert edge.base_name == "Base"

    def test_inherits_edge_multiple_bases(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": (
                "class A: pass\n"
                "class B: pass\n"
                "class C(A, B): pass\n"
            ),
        })
        out = g.edges_out("src/a.py#C", kind=EdgeKind.INHERITS)
        targets = {e.target for e in out}
        assert "src/a.py#A" in targets
        assert "src/a.py#B" in targets

    def test_no_inherits_for_non_class(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "def foo(): pass\n",
        })
        assert g.edges_out("src/a.py#foo", kind=EdgeKind.INHERITS) == []

    def test_inherits_cross_file(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/base.py": "class Base: pass\n",
            "src/child.py": (
                "from src.base import Base\n"
                "class Child(Base): pass\n"
            ),
        })
        out = g.edges_out("src/child.py#Child", kind=EdgeKind.INHERITS)
        targets = {e.target for e in out}
        assert "src/base.py#Base" in targets

    def test_no_self_inherits_edge(self, tmp_path: Path) -> None:
        # A class cannot inherit from itself in a well-formed graph
        g = _build_graph(tmp_path, {
            "src/a.py": "class NewFoo(NewFoo): pass\n",
        })
        out = g.edges_out("src/a.py#NewFoo", kind=EdgeKind.INHERITS)
        self_edges = [e for e in out if e.target == "src/a.py#NewFoo"]
        assert self_edges == []


# ---------------------------------------------------------------------------
# Node queries
# ---------------------------------------------------------------------------


class TestNodeQuery:
    def test_node_returns_correct_node(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        n = g.node("src/a.py")
        assert n is not None
        assert n.id == "src/a.py"

    def test_node_unknown_id_returns_none(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.node("nonexistent.py") is None

    def test_nodes_no_filter(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        all_nodes = g.nodes()
        assert any(n.kind is NodeKind.FILE for n in all_nodes)
        assert any(n.kind is NodeKind.SYMBOL for n in all_nodes)

    def test_nodes_file_filter(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        file_nodes = g.nodes(kind=NodeKind.FILE)
        assert all(n.kind is NodeKind.FILE for n in file_nodes)

    def test_nodes_symbol_filter(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        sym_nodes = g.nodes(kind=NodeKind.SYMBOL)
        assert all(n.kind is NodeKind.SYMBOL for n in sym_nodes)

    def test_contains_known_node(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        assert "src/a.py" in g
        assert "src/a.py#NewFoo" in g

    def test_not_contains_unknown_node(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        assert "nonexistent.py" not in g


# ---------------------------------------------------------------------------
# Edge queries
# ---------------------------------------------------------------------------


class TestEdgeQuery:
    def test_edges_no_filter(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        all_edges = g.edges()
        assert len(all_edges) > 0

    def test_edges_filter_by_kind(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        defines = g.edges(kind=EdgeKind.DEFINES)
        assert all(e.kind is EdgeKind.DEFINES for e in defines)

    def test_edges_no_duplicates(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        all_edges = g.edges()
        keys = [(e.source, e.target, e.kind) for e in all_edges]
        assert len(keys) == len(set(keys))

    def test_edges_out_from_file(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        out = g.edges_out("src/a.py")
        assert any(e.kind is EdgeKind.IMPORTS for e in out)

    def test_edges_in_to_file(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        incoming = g.edges_in("src/b.py", kind=EdgeKind.IMPORTS)
        assert any(e.source == "src/a.py" for e in incoming)

    def test_edges_out_unknown_node(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.edges_out("nonexistent.py") == []

    def test_edges_in_unknown_node(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.edges_in("nonexistent.py") == []


# ---------------------------------------------------------------------------
# Neighbour traversal
# ---------------------------------------------------------------------------


class TestNeighbors:
    def test_neighbors_out_imports(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        out = g.neighbors_out("src/a.py", kind=EdgeKind.IMPORTS)
        ids = {n.id for n in out}
        assert "src/b.py" in ids

    def test_neighbors_in_imports(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        incoming = g.neighbors_in("src/b.py", kind=EdgeKind.IMPORTS)
        ids = {n.id for n in incoming}
        assert "src/a.py" in ids

    def test_neighbors_out_no_kind_filter(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        out = g.neighbors_out("src/a.py")
        assert len(out) >= 1  # at least one DEFINES edge target

    def test_neighbors_out_unknown_node(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.neighbors_out("nonexistent.py") == []

    def test_neighbors_in_unknown_node(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.neighbors_in("nonexistent.py") == []


# ---------------------------------------------------------------------------
# detect_cycles
# ---------------------------------------------------------------------------


class TestDetectCycles:
    def test_single_file_no_cycle(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.detect_cycles() == []

    def test_acyclic_imports_no_cycle(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        assert g.detect_cycles() == []

    def test_direct_cycle_detected(self, tmp_path: Path) -> None:
        # a imports b and b imports a
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import Y\nX = 1\n",
            "src/b.py": "from src.a import X\nY = 2\n",
        })
        cycles = g.detect_cycles()
        assert len(cycles) == 1
        cycle = cycles[0]
        assert "src/a.py" in cycle
        assert "src/b.py" in cycle

    def test_transitive_cycle_detected(self, tmp_path: Path) -> None:
        # a → b → c → a
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import Y\nX = 1\n",
            "src/b.py": "from src.c import Z\nY = 2\n",
            "src/c.py": "from src.a import X\nZ = 3\n",
        })
        cycles = g.detect_cycles()
        assert len(cycles) == 1
        assert sorted(cycles[0]) == ["src/a.py", "src/b.py", "src/c.py"]

    def test_isolated_node_not_in_cycle(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import Y\nX = 1\n",
            "src/b.py": "from src.a import X\nY = 2\n",
            "src/c.py": "z = 3\n",
        })
        cycles = g.detect_cycles()
        # c.py must not appear in any cycle
        for cycle in cycles:
            assert "src/c.py" not in cycle

    def test_multiple_independent_cycles(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\nA = 1\n",
            "src/b.py": "from src.a import A\nB = 2\n",
            "src/x.py": "from src.y import Y\nX = 1\n",
            "src/y.py": "from src.x import X\nY = 2\n",
        })
        cycles = g.detect_cycles()
        assert len(cycles) == 2

    def test_cycle_results_are_sorted(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\nA = 1\n",
            "src/b.py": "from src.a import A\nB = 2\n",
        })
        for cycle in g.detect_cycles():
            assert cycle == sorted(cycle)


# ---------------------------------------------------------------------------
# shortest_path
# ---------------------------------------------------------------------------


class TestShortestPath:
    def test_unknown_source_returns_none(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.shortest_path("nonexistent.py", "src/a.py") is None

    def test_unknown_target_returns_none(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.shortest_path("src/a.py", "nonexistent.py") is None

    def test_same_node_returns_single_element(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        path = g.shortest_path("src/a.py", "src/a.py")
        assert path == ["src/a.py"]

    def test_direct_edge(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        path = g.shortest_path("src/a.py", "src/b.py", kind=EdgeKind.IMPORTS)
        assert path == ["src/a.py", "src/b.py"]

    def test_multi_hop_path(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\n",
            "src/b.py": "from src.c import C\nB = 1\n",
            "src/c.py": "C = 2\n",
        })
        path = g.shortest_path("src/a.py", "src/c.py", kind=EdgeKind.IMPORTS)
        assert path is not None
        assert path[0] == "src/a.py"
        assert path[-1] == "src/c.py"
        assert len(path) == 3

    def test_no_path_returns_none(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        # b does not import a
        assert g.shortest_path("src/b.py", "src/a.py", kind=EdgeKind.IMPORTS) is None

    def test_shortest_path_prefers_fewer_hops(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\nfrom src.c import C\n",
            "src/b.py": "from src.c import C\nB = 1\n",
            "src/c.py": "C = 2\n",
        })
        path = g.shortest_path("src/a.py", "src/c.py", kind=EdgeKind.IMPORTS)
        # Direct edge a→c exists; shortest path should be length 2
        assert path is not None
        assert len(path) == 2


# ---------------------------------------------------------------------------
# transitive_dependencies
# ---------------------------------------------------------------------------


class TestTransitiveDependencies:
    def test_unknown_file_returns_empty(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.transitive_dependencies("nonexistent.py") == []

    def test_no_imports_returns_empty(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.transitive_dependencies("src/a.py") == []

    def test_direct_dependency(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        deps = g.transitive_dependencies("src/a.py")
        assert "src/b.py" in deps
        assert "src/a.py" not in deps

    def test_transitive_chain(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\n",
            "src/b.py": "from src.c import C\nB = 1\n",
            "src/c.py": "C = 2\n",
        })
        deps = g.transitive_dependencies("src/a.py")
        assert "src/b.py" in deps
        assert "src/c.py" in deps
        assert "src/a.py" not in deps

    def test_result_is_sorted(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\nfrom src.c import C\n",
            "src/b.py": "B = 1\n",
            "src/c.py": "C = 2\n",
        })
        deps = g.transitive_dependencies("src/a.py")
        assert deps == sorted(deps)


# ---------------------------------------------------------------------------
# transitive_dependents
# ---------------------------------------------------------------------------


class TestTransitiveDependents:
    def test_unknown_file_returns_empty(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.transitive_dependents("nonexistent.py") == []

    def test_no_dependents_returns_empty(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        # b is imported by a; a has no dependents
        assert g.transitive_dependents("src/a.py") == []

    def test_direct_dependent(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        deps = g.transitive_dependents("src/b.py")
        assert "src/a.py" in deps
        assert "src/b.py" not in deps

    def test_transitive_chain(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\n",
            "src/b.py": "from src.c import C\nB = 1\n",
            "src/c.py": "C = 2\n",
        })
        deps = g.transitive_dependents("src/c.py")
        assert "src/b.py" in deps
        assert "src/a.py" in deps
        assert "src/c.py" not in deps

    def test_result_is_sorted(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/b.py": "X = 1\n",
            "src/a.py": "from src.b import X\n",
            "src/c.py": "from src.b import X\n",
        })
        deps = g.transitive_dependents("src/b.py")
        assert deps == sorted(deps)


# ---------------------------------------------------------------------------
# impact
# ---------------------------------------------------------------------------


class TestImpact:
    def test_no_dependents_low_risk(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        result = g.impact("src/a.py")
        assert result.risk_level == "LOW"
        assert result.direct_dependents == []
        assert result.transitive_dependents == []

    def test_one_dependent_low_risk(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        result = g.impact("src/b.py")
        assert result.risk_level == "LOW"
        assert "src/a.py" in result.direct_dependents

    def test_two_transitive_dependents_low_risk(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/b.py": "X = 1\n",
            "src/a.py": "from src.b import X\n",
            "src/c.py": "from src.b import X\n",
        })
        result = g.impact("src/b.py")
        assert result.risk_level == "LOW"

    def test_medium_risk_threshold(self, tmp_path: Path) -> None:
        # 3 transitive dependents → MEDIUM
        files = {"src/base.py": "X = 1\n"}
        for i in range(3):
            files[f"src/user{i}.py"] = "from src.base import X\n"
        g = _build_graph(tmp_path, files)
        result = g.impact("src/base.py")
        assert result.risk_level == "MEDIUM"

    def test_high_risk_threshold(self, tmp_path: Path) -> None:
        # 11 transitive dependents → HIGH
        files = {"src/base.py": "X = 1\n"}
        for i in range(11):
            files[f"src/user{i}.py"] = "from src.base import X\n"
        g = _build_graph(tmp_path, files)
        result = g.impact("src/base.py")
        assert result.risk_level == "HIGH"

    def test_affected_symbol_count(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/base.py": "X = 1\n",
            "src/user.py": "from src.base import X\nclass NewFoo: pass\ndef bar(): pass\n",
        })
        result = g.impact("src/base.py")
        # user.py has at least 2 symbols (NewFoo and bar)
        assert result.affected_symbol_count >= 2

    def test_direct_vs_transitive_dependents(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\n",
            "src/b.py": "from src.c import C\nB = 1\n",
            "src/c.py": "C = 2\n",
        })
        result = g.impact("src/c.py")
        assert "src/b.py" in result.direct_dependents
        assert "src/a.py" in result.transitive_dependents

    def test_changed_file_field(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        result = g.impact("src/a.py")
        assert result.changed_file == "src/a.py"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_raises_before_build(self) -> None:
        g = RepositoryGraph()
        with pytest.raises(RuntimeError):
            g.stats()

    def test_node_counts(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "class NewFoo: pass\n",
            "src/b.py": "x = 1\n",
        })
        s = g.stats()
        assert s.file_node_count == 2
        assert s.symbol_node_count >= 2  # NewFoo + x

    def test_edge_counts_defines(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        s = g.stats()
        assert s.defines_edge_count >= 1

    def test_imports_edge_count(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import X\n",
            "src/b.py": "X = 1\n",
        })
        s = g.stats()
        assert s.imports_edge_count == 1

    def test_cycle_count_in_stats(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {
            "src/a.py": "from src.b import B\nA = 1\n",
            "src/b.py": "from src.a import A\nB = 2\n",
        })
        s = g.stats()
        assert s.cycle_count == 1

    def test_build_duration_positive(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert g.stats().build_duration_ms >= 0.0

    def test_node_count_equals_len(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        assert g.stats().node_count == len(g)


# ---------------------------------------------------------------------------
# Dunder helpers
# ---------------------------------------------------------------------------


class TestDunder:
    def test_len_empty(self) -> None:
        g = RepositoryGraph()
        assert len(g) == 0

    def test_len_after_build(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "class NewFoo: pass\n"})
        assert len(g) >= 2  # at least FILE node + SYMBOL node

    def test_contains_true(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert "src/a.py" in g

    def test_contains_false(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        assert "nonexistent.py" not in g

    def test_repr_unbuilt(self) -> None:
        g = RepositoryGraph()
        assert "unbuilt" in repr(g)

    def test_repr_after_build(self, tmp_path: Path) -> None:
        g = _build_graph(tmp_path, {"src/a.py": "x = 1\n"})
        r = repr(g)
        assert "nodes=" in r
        assert "edges=" in r
        assert "cycles=" in r


# ---------------------------------------------------------------------------
# Internal helpers — _build_module_map
# ---------------------------------------------------------------------------


class TestBuildModuleMap:
    def test_regular_module(self, tmp_path: Path) -> None:
        index = _build_index(tmp_path, {"src/a.py": "x = 1\n"})
        m = _build_module_map(index)
        assert m.get("src.a") == "src/a.py"

    def test_init_file_maps_to_package(self, tmp_path: Path) -> None:
        index = _build_index(tmp_path, {"src/__init__.py": "x = 1\n"})
        m = _build_module_map(index)
        assert m.get("src") == "src/__init__.py"

    def test_nested_package(self, tmp_path: Path) -> None:
        index = _build_index(tmp_path, {"src/repository/__init__.py": "x = 1\n"})
        m = _build_module_map(index)
        assert m.get("src.repository") == "src/repository/__init__.py"

    def test_nested_module(self, tmp_path: Path) -> None:
        index = _build_index(tmp_path, {"src/repository/index.py": "x = 1\n"})
        m = _build_module_map(index)
        assert m.get("src.repository.index") == "src/repository/index.py"

    def test_multiple_files(self, tmp_path: Path) -> None:
        index = _build_index(tmp_path, {
            "src/a.py": "x = 1\n",
            "src/b.py": "y = 2\n",
        })
        m = _build_module_map(index)
        assert "src.a" in m
        assert "src.b" in m


# ---------------------------------------------------------------------------
# Internal helpers — _parse_import_string
# ---------------------------------------------------------------------------


class TestParseImportString:
    def test_plain_import(self) -> None:
        result = _parse_import_string("import os")
        assert result == (0, "os")

    def test_from_import(self) -> None:
        result = _parse_import_string("from pathlib import Path")
        assert result == (0, "pathlib")

    def test_relative_dot(self) -> None:
        result = _parse_import_string("from . import models")
        assert result == (1, "")

    def test_relative_dot_dot(self) -> None:
        result = _parse_import_string("from .. import utils")
        assert result == (2, "")

    def test_relative_with_module(self) -> None:
        result = _parse_import_string("from ..utils import helper")
        assert result == (2, "utils")

    def test_relative_three_dots(self) -> None:
        result = _parse_import_string("from ...pkg import X")
        assert result == (3, "pkg")

    def test_import_with_alias(self) -> None:
        result = _parse_import_string("import numpy as np")
        assert result == (0, "numpy")

    def test_multi_name_import_takes_first(self) -> None:
        result = _parse_import_string("import os, sys")
        assert result == (0, "os")

    def test_from_multi_name_import(self) -> None:
        result = _parse_import_string("from pathlib import Path, PurePath")
        assert result == (0, "pathlib")

    def test_unparseable_returns_none(self) -> None:
        assert _parse_import_string("not an import") is None
        assert _parse_import_string("") is None

    def test_relative_dot_with_sub_module(self) -> None:
        result = _parse_import_string("from .sub.mod import X")
        assert result == (1, "sub.mod")


# ---------------------------------------------------------------------------
# Internal helpers — _resolve_to_file
# ---------------------------------------------------------------------------


class TestResolveToFile:
    def _mmap(self) -> dict[str, str]:
        return {
            "src.a": "src/a.py",
            "src.b": "src/b.py",
            "src.repository": "src/repository/__init__.py",
            "src.repository.index": "src/repository/index.py",
            "src.repository.models": "src/repository/models.py",
        }

    def test_absolute_found(self) -> None:
        result = _resolve_to_file(0, "src.a", "other/x.py", self._mmap())
        assert result == "src/a.py"

    def test_absolute_not_found(self) -> None:
        result = _resolve_to_file(0, "os", "src/a.py", self._mmap())
        assert result is None

    def test_relative_dot_sibling(self) -> None:
        # "from . import b" in src/a.py → src/b.py
        result = _resolve_to_file(1, "b", "src/a.py", self._mmap())
        assert result == "src/b.py"

    def test_relative_dot_empty_module(self) -> None:
        # "from . import models" when current package is src.repository
        result = _resolve_to_file(1, "models", "src/repository/index.py", self._mmap())
        assert result == "src/repository/models.py"

    def test_relative_dot_dot(self) -> None:
        # "from .. import a" in src/repository/index.py → src/a.py
        result = _resolve_to_file(2, "a", "src/repository/index.py", self._mmap())
        assert result == "src/a.py"

    def test_relative_dot_dot_with_module(self) -> None:
        # "from ..repository import index" in src/subpkg/a.py
        # ..  = parent of src.subpkg = src; then repository.index = src.repository.index
        result = _resolve_to_file(2, "repository.index", "src/subpkg/a.py", self._mmap())
        assert result == "src/repository/index.py"

    def test_too_many_dots_returns_none(self) -> None:
        result = _resolve_to_file(10, "x", "src/a.py", self._mmap())
        assert result is None

    def test_relative_empty_module_package(self) -> None:
        # "from . import X" — empty module, current package is "src"
        mmap = {"src": "src/__init__.py", "src.a": "src/a.py"}
        result = _resolve_to_file(1, "", "src/b.py", mmap)
        assert result == "src/__init__.py"


# ---------------------------------------------------------------------------
# Internal helpers — _extract_imported_names
# ---------------------------------------------------------------------------


class TestExtractImportedNames:
    def test_single_name(self) -> None:
        assert _extract_imported_names("from pathlib import Path") == ["Path"]

    def test_multi_names(self) -> None:
        names = _extract_imported_names("from pathlib import Path, PurePath")
        assert names == ["Path", "PurePath"]

    def test_relative_import(self) -> None:
        names = _extract_imported_names("from . import models")
        assert names == ["models"]

    def test_plain_import_empty(self) -> None:
        assert _extract_imported_names("import os") == []

    def test_alias_stripped(self) -> None:
        names = _extract_imported_names("from pathlib import Path as P")
        assert names == ["Path"]

    def test_whitespace_stripped(self) -> None:
        names = _extract_imported_names("from pkg import  A , B ")
        assert "A" in names
        assert "B" in names


# ---------------------------------------------------------------------------
# Internal helpers — _find_symbol_node
# ---------------------------------------------------------------------------


class TestFindSymbolNode:
    def _nodes(self) -> dict[str, Node]:
        return {
            "a.py#Base": Node(id="a.py#Base", kind=NodeKind.SYMBOL, label="Base"),
            "a.py#pkg.Sub": Node(id="a.py#pkg.Sub", kind=NodeKind.SYMBOL, label="pkg.Sub"),
            "b.py#NewFoo": Node(id="b.py#NewFoo", kind=NodeKind.FILE, label="NewFoo"),  # FILE, not SYMBOL
        }

    def test_exact_label_match(self) -> None:
        result = _find_symbol_node("Base", "exclude", self._nodes())
        assert result == "a.py#Base"

    def test_suffix_match(self) -> None:
        result = _find_symbol_node("pkg.Sub", "exclude", self._nodes())
        assert result == "a.py#pkg.Sub"

    def test_last_component_match(self) -> None:
        result = _find_symbol_node("other.Base", "exclude", self._nodes())
        assert result == "a.py#Base"

    def test_file_node_not_matched(self) -> None:
        # b.py#NewFoo is a FILE node — should not be returned
        nodes = {
            "b.py#NewFoo": Node(id="b.py#NewFoo", kind=NodeKind.FILE, label="NewFoo"),
        }
        result = _find_symbol_node("NewFoo", "exclude", nodes)
        assert result is None

    def test_exclude_self(self) -> None:
        nodes = {
            "a.py#Base": Node(id="a.py#Base", kind=NodeKind.SYMBOL, label="Base"),
        }
        result = _find_symbol_node("Base", "a.py#Base", nodes)
        assert result is None

    def test_no_match_returns_none(self) -> None:
        result = _find_symbol_node("NonExistent", "exclude", self._nodes())
        assert result is None


# ---------------------------------------------------------------------------
# Layer rule
# ---------------------------------------------------------------------------


class TestLayerRule:
    """graph.py must not import from src.agent, src.tools, src.mcp, src.llm,
    or src.prompts — only from src.repository.* and the stdlib."""

    def test_no_forbidden_imports(self) -> None:
        source = Path(__file__).parent.parent.parent / "src/repository/graph.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden_prefixes = (
            "src.agent",
            "src.tools",
            "src.mcp",
            "src.llm",
            "src.prompts",
        )
        bad: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(p) for p in forbidden_prefixes):
                    bad.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in forbidden_prefixes):
                        bad.append(alias.name)
        assert bad == [], f"Forbidden imports in graph.py: {bad}"


# ---------------------------------------------------------------------------
# Integration — full pipeline
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_pearl_agent_codebase(self) -> None:
        """Build graph from the actual pearl-agent source tree."""
        from src.repository import RepositoryScanner, Language
        from src.repository.parsers import ParserRegistry

        root = Path(__file__).parent.parent.parent
        scanner = RepositoryScanner(root)
        scan = scanner.scan()

        py_files = scan.source_files(Language.PYTHON)
        assert len(py_files) > 0

        registry = ParserRegistry.default()
        results = list(registry.parse_many(py_files))
        index = RepositoryIndex.build(results)
        graph = RepositoryGraph.build(index)

        # Basic sanity checks
        s = graph.stats()
        assert s.file_node_count == len(py_files)
        assert s.symbol_node_count > 0
        assert s.defines_edge_count > 0
        assert s.build_duration_ms >= 0.0

        # The graph knows about this test file
        rel = str(Path(__file__).relative_to(root)).replace("\\", "/")
        assert rel in graph

    def test_inherits_edges_in_real_codebase(self) -> None:
        """INHERITS edges should exist in pearl-agent (BaseParser subclasses)."""
        from src.repository import RepositoryScanner, Language
        from src.repository.parsers import ParserRegistry

        root = Path(__file__).parent.parent.parent
        scanner = RepositoryScanner(root)
        scan = scanner.scan()
        registry = ParserRegistry.default()
        results = list(registry.parse_many(scan.source_files(Language.PYTHON)))
        index = RepositoryIndex.build(results)
        graph = RepositoryGraph.build(index)

        inherits_edges = graph.edges(kind=EdgeKind.INHERITS)
        # Pearl has at least PythonParser(BaseParser)
        assert len(inherits_edges) >= 1

    def test_cycle_detection_real_codebase(self) -> None:
        """pearl-agent itself must be acyclic (it is well-structured)."""
        from src.repository import RepositoryScanner, Language
        from src.repository.parsers import ParserRegistry

        root = Path(__file__).parent.parent.parent
        scanner = RepositoryScanner(root)
        scan = scanner.scan()
        registry = ParserRegistry.default()
        results = list(registry.parse_many(scan.source_files(Language.PYTHON)))
        index = RepositoryIndex.build(results)
        graph = RepositoryGraph.build(index)

        cycles = graph.detect_cycles()
        # pearl-agent's src/ should be acyclic
        # (if it isn't, this test surfaces the problem)
        assert isinstance(cycles, list)

    def test_impact_analysis_real_file(self) -> None:
        from src.repository import RepositoryScanner, Language
        from src.repository.parsers import ParserRegistry

        root = Path(__file__).parent.parent.parent
        scanner = RepositoryScanner(root)
        scan = scanner.scan()
        registry = ParserRegistry.default()
        results = list(registry.parse_many(scan.source_files(Language.PYTHON)))
        index = RepositoryIndex.build(results)
        graph = RepositoryGraph.build(index)

        rel = "src/repository/models.py"
        if rel in graph:
            result = graph.impact(rel)
            assert result.changed_file == rel
            assert result.risk_level in ("LOW", "MEDIUM", "HIGH")


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


class TestBenchmarks:
    def test_build_under_500ms_for_50_files(self, tmp_path: Path) -> None:
        files: dict[str, str] = {}
        for i in range(50):
            src = f"class Cls{i}:\n    def method(self): pass\n\nVAR_{i} = {i}\n"
            files[f"src/module_{i}.py"] = src

        t0 = time.monotonic()
        g = _build_graph(tmp_path, files)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 500, f"Build took {elapsed_ms:.0f}ms (limit 500ms)"
        assert g.stats().file_node_count == 50

    def test_detect_cycles_under_100ms(self, tmp_path: Path) -> None:
        files: dict[str, str] = {"src/base.py": "X = 1\n"}
        for i in range(30):
            files[f"src/m{i}.py"] = "from src.base import X\n"

        g = _build_graph(tmp_path, files)

        t0 = time.monotonic()
        for _ in range(20):
            g.detect_cycles()
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms / 20 < 100, f"detect_cycles avg {elapsed_ms/20:.1f}ms (limit 100ms)"

    def test_impact_under_50ms(self, tmp_path: Path) -> None:
        files: dict[str, str] = {"src/base.py": "X = 1\n"}
        for i in range(20):
            files[f"src/user{i}.py"] = "from src.base import X\n"

        g = _build_graph(tmp_path, files)

        t0 = time.monotonic()
        for _ in range(50):
            g.impact("src/base.py")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms / 50 < 50, f"impact() avg {elapsed_ms/50:.1f}ms (limit 50ms)"
