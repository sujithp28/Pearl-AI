"""
Tests for P5 — PageRank boost in SemanticContextBuilder._rank().

The PageRank step boosts already-term-matched files by their centrality
in the IMPORTS graph.  A file with many reverse-IMPORTS (depended on by
many others) should score higher than an equally-matched leaf file.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pathlib import Path

from src.repository.context import ContextConfig, SemanticContextBuilder
from src.repository.graph import Edge, EdgeKind, Node, NodeKind, RepositoryGraph
from src.repository.index import RepositoryIndex, SymbolEntry
from src.repository.models import FileInfo, Language
from src.repository.parsers import SymbolDef, SymbolKind


# ---------------------------------------------------------------------------
# Minimal index / graph builders
# ---------------------------------------------------------------------------


def _make_file_info(relative_path: str) -> FileInfo:
    return FileInfo(
        path=Path(relative_path),
        relative_path=relative_path,
        extension=".py",
        language=Language.PYTHON,
        size=100,
        modified_at=0.0,
        content_hash="",
    )


def _make_symbol(
    name: str,
    file: str,
    kind: str = "function",
    line_start: int = 1,
    line_end: int = 10,
) -> SymbolEntry:
    sym = SymbolDef(
        name=name,
        qualified_name=name,
        kind=SymbolKind(kind),
        line_start=line_start,
        line_end=line_end,
    )
    fi = _make_file_info(file)
    return SymbolEntry(
        symbol=sym,
        file_info=fi,
        relative_path=file,
        language=Language.PYTHON,
    )


def _make_index(*entries: SymbolEntry) -> RepositoryIndex:
    idx = MagicMock(spec=RepositoryIndex)
    files_by_path: dict[str, list[SymbolEntry]] = {}
    for e in entries:
        files_by_path.setdefault(e.relative_path, []).append(e)

    idx.indexed_files.return_value = [
        _make_file_info(p) for p in files_by_path
    ]
    idx.symbols_in_file.side_effect = lambda p: files_by_path.get(p, [])
    idx.lookup.side_effect = lambda name: [
        e for e in entries if e.symbol.name == name
    ]
    return idx


def _make_graph(**edges: list[str]) -> RepositoryGraph:
    """Build a RepositoryGraph with IMPORTS edges.

    edges: dict of {source: [target, ...]} for IMPORTS edges.
    """
    g = RepositoryGraph()

    def _ensure_file_node(path: str) -> None:
        if g.node(path) is None:
            g._add_node(Node(id=path, kind=NodeKind.FILE, label=path))

    for source, targets in edges.items():
        _ensure_file_node(source)
        for t in targets:
            _ensure_file_node(t)
            g._add_edge(Edge(source=source, target=t, kind=EdgeKind.IMPORTS))
    return g


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPageRankBoost:
    def test_central_file_scores_higher_than_leaf(self):
        """A hub file (many reverse-IMPORTS) beats an equally term-matched leaf."""
        hub = _make_symbol("patch_manager", "src/hub.py")
        leaf = _make_symbol("patch_manager", "src/leaf.py")
        index = _make_index(hub, leaf)

        # hub.py is imported by 5 files; leaf.py is imported by no one
        graph = _make_graph(
            **{
                "src/a.py": ["src/hub.py"],
                "src/b.py": ["src/hub.py"],
                "src/c.py": ["src/hub.py"],
                "src/d.py": ["src/hub.py"],
                "src/e.py": ["src/hub.py"],
            }
        )

        builder = SemanticContextBuilder(ContextConfig(max_files=10))
        service = MagicMock()
        service.index = index
        service.graph = graph
        service.root = __import__("pathlib").Path(".")

        with patch.object(builder, "_render_candidates", return_value=[]), \
             patch.object(builder, "_relevant_cycles", return_value=[]):
            # Call _rank() directly
            candidates = builder._rank(
                "patch_manager",
                {"patch_manager"},
                index,
                graph,
                None,
                service.root,
            )

        paths = [c.path for c in candidates]
        assert "src/hub.py" in paths
        assert "src/leaf.py" in paths

        hub_score = next(c.score for c in candidates if c.path == "src/hub.py")
        leaf_score = next(c.score for c in candidates if c.path == "src/leaf.py")
        assert hub_score > leaf_score, (
            f"Hub ({hub_score:.3f}) should outrank leaf ({leaf_score:.3f})"
        )

    def test_pagerank_reasons_added(self):
        """Files boosted by PageRank should include a 'pagerank=' reason."""
        sym = _make_symbol("executor", "src/agent/executor.py")
        index = _make_index(sym)
        graph = _make_graph(**{"src/main.py": ["src/agent/executor.py"]})

        builder = SemanticContextBuilder(ContextConfig(max_files=10))
        candidates = builder._rank(
            "executor",
            {"executor"},
            index,
            graph,
            None,
            __import__("pathlib").Path("."),
        )

        ranked = next(c for c in candidates if c.path == "src/agent/executor.py")
        assert any("pagerank=" in r for r in ranked.reasons), (
            f"Expected 'pagerank=' in reasons, got: {ranked.reasons}"
        )

    def test_pagerank_failure_is_non_fatal(self):
        """If graph.pagerank() raises, _rank() still returns term-matched files."""
        sym = _make_symbol("patch_manager", "src/pm.py")
        index = _make_index(sym)

        graph = MagicMock(spec=RepositoryGraph)
        graph.pagerank.side_effect = RuntimeError("NetworkX not available")
        graph.edges_in.return_value = []
        graph.edges_out.return_value = []

        builder = SemanticContextBuilder(ContextConfig(max_files=10))
        candidates = builder._rank(
            "patch_manager",
            {"patch_manager"},
            index,
            graph,
            None,
            __import__("pathlib").Path("."),
        )

        assert any(c.path == "src/pm.py" for c in candidates)

    def test_pagerank_does_not_add_new_files(self):
        """PageRank should boost existing candidates only, not add un-matched files."""
        sym = _make_symbol("token_budget", "src/llm/token_budget.py")
        index = _make_index(sym)

        # Five files import token_budget.py but have no term match themselves
        graph = _make_graph(
            **{
                "src/a.py": ["src/llm/token_budget.py"],
                "src/b.py": ["src/llm/token_budget.py"],
            }
        )

        builder = SemanticContextBuilder(ContextConfig(max_files=10, expansion_hops=0))
        candidates = builder._rank(
            "token_budget",
            {"token_budget"},
            index,
            graph,
            None,
            __import__("pathlib").Path("."),
        )

        # With expansion_hops=0, only the term-matched file should be returned
        assert len(candidates) == 1
        assert candidates[0].path == "src/llm/token_budget.py"

    def test_empty_graph_falls_back_gracefully(self):
        """An empty graph produces zero PageRank; term-match ranking still works."""
        sym = _make_symbol("verify", "src/agent/verification.py")
        index = _make_index(sym)
        graph = RepositoryGraph()  # no nodes, no edges

        builder = SemanticContextBuilder(ContextConfig(max_files=10))
        candidates = builder._rank(
            "verify",
            {"verify"},
            index,
            graph,
            None,
            __import__("pathlib").Path("."),
        )

        assert any(c.path == "src/agent/verification.py" for c in candidates)
