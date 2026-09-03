"""
Pearl Repository Intelligence — Semantic Context Builder (Phase 6).

``SemanticContextBuilder`` uses the full M3 stack — ``RepositoryIndex``
and ``RepositoryGraph`` — to build the smallest, highest-quality context
string for the LLM planner.

What makes this "semantic" vs. the older ``ContextBuilder`` in
``src/tools/context_manager.py``:

1. **Richer symbol matching** — matches against the fully-qualified name
   (``"PatchManager.stage"``), the docstring first line, and decorators,
   not just the flat symbol name.

2. **Graph-aware expansion** — after scoring files by term match, the top
   candidates are expanded by one IMPORTS hop so the LLM also sees the
   files that *use* the matched symbols.  A query for "PatchManager" will
   include ``edit_tools.py`` because it imports ``PatchManager``; the LLM
   then understands the full approval contract without an extra round-trip.

3. **Precise source extraction** — large files are compressed using the
   ``line_start``/``line_end`` ranges from Phase 4 ``SymbolDef``, so the
   LLM sees the exact definition of each matched symbol rather than a
   loose approximate block.

4. **Cycle awareness** — if selected files are part of circular import
   groups, the context header notes this so the LLM can reason about
   coupling correctly.

The output is a plain ``str`` that fits directly into
``Planner.plan(..., workspace_context=...)`` without the Planner knowing
or caring what produced it.

Usage::

    from src.repository.context import SemanticContextBuilder, ContextConfig
    from src.repository.service import RepositoryService
    from pathlib import Path

    svc = RepositoryService.get_or_build(Path("."))
    builder = SemanticContextBuilder()
    ctx = builder.build("fix the patch manager approval flow", svc)
    print(ctx[:500])
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.repository.graph import EdgeKind, RepositoryGraph
from src.repository.index import RepositoryIndex, SymbolEntry

if TYPE_CHECKING:
    from src.memory.workspace_memory import WorkspaceMemory
    from src.repository.service import RepositoryService

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "to",
        "for",
        "and",
        "or",
        "in",
        "on",
        "at",
        "of",
        "with",
        "this",
        "that",
        "please",
        "can",
        "you",
        "it",
        "be",
        "as",
        "by",
        "from",
        "into",
        "about",
        "fix",
        "add",
        "make",
        "get",
        "set",
        "use",
        "do",
        "how",
        "why",
        "what",
        "when",
        "where",
        "which",
        "who",
        "my",
        "our",
    }
)

# ~4 characters per token — intentionally imprecise; only used for budgeting.
_CHARS_PER_TOKEN = 4

# Files shorter than this are included in full; longer files are compressed
# to show only the matched symbol bodies.
_FULL_CONTENT_THRESHOLD_LINES = 150

# Maximum symbols shown per file in compressed mode.
_MAX_SYMBOLS_PER_FILE = 6


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)} - _STOPWORDS


def _term_matches(haystack: str, terms: set[str]) -> bool:
    """Return True if any term appears as a substring of *haystack* (lowercased)."""
    if not haystack or not terms:
        return False
    low = haystack.lower()
    return any(len(t) >= 3 and t in low for t in terms)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContextConfig:
    """Budgets and thresholds for :class:`SemanticContextBuilder`.

    Parameters
    ----------
    max_context_tokens:
        Hard cap on the total estimated token cost of the output string.
        Files that would push the budget over this limit are omitted.
    max_files:
        Maximum number of files included in the output, regardless of
        token budget.
    expansion_hops:
        Number of IMPORTS graph hops to follow when expanding the initial
        candidate set.  ``1`` means direct dependencies only.  Set to
        ``0`` to disable graph expansion entirely.
    """

    max_context_tokens: int = 6000
    max_files: int = 12
    expansion_hops: int = 1


# ---------------------------------------------------------------------------
# Ranked file record
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RankedFile:
    """One file's relevance score and the signals that produced it."""

    path: str
    score: float
    reasons: list[str] = field(default_factory=list)
    matched_symbols: list[SymbolEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SemanticContextBuilder
# ---------------------------------------------------------------------------


class SemanticContextBuilder:
    """Graph-aware, symbol-precise context builder powered by M3 data.

    Build via the default constructor — no required arguments.

    Parameters
    ----------
    config:
        Token/file budgets and graph expansion depth.  Defaults apply
        when omitted.
    """

    def __init__(self, config: ContextConfig | None = None) -> None:
        self.config = config or ContextConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        query: str,
        service: "RepositoryService",
        workspace_memory: "WorkspaceMemory | None" = None,
    ) -> str:
        """Return an LLM-ready context string for *query*.

        Returns ``""`` when no relevant files are found or the M3 index
        is empty (e.g. an empty repository).

        Parameters
        ----------
        query:
            The user's prompt or task description.
        service:
            A :class:`~src.repository.service.RepositoryService` for the
            workspace root.  The index and graph are built lazily on
            first access.
        workspace_memory:
            Optional session signals (recently edited files / created
            symbols) used as an extra ranking boost.
        """
        index = service.index
        graph = service.graph

        if not list(index.indexed_files()):
            return ""

        terms = _tokenize(query)
        if not terms:
            return ""

        candidates = self._rank(
            query, terms, index, graph, workspace_memory, service.root
        )
        if not candidates:
            return ""

        sections = self._render_candidates(candidates, index, graph, service.root)
        if not sections:
            return ""

        cycles = self._relevant_cycles(graph, {c.path for c in candidates})
        header = self._build_header(query, cycles)
        return header + "\n\n" + "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def _rank(
        self,
        query: str,
        terms: set[str],
        index: RepositoryIndex,
        graph: RepositoryGraph,
        workspace_memory: "WorkspaceMemory | None",
        root: Path,
    ) -> list[RankedFile]:
        scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}
        matched: dict[str, list[SymbolEntry]] = {}

        def boost(file: str, amount: float, reason: str) -> None:
            scores[file] = scores.get(file, 0.0) + amount
            reasons.setdefault(file, []).append(reason)

        # --- Symbol matching ---
        for entry in self._all_entries(index):
            fp = entry.relative_path
            sym = entry.symbol

            # Qualified name (e.g. "PatchManager.stage")
            if _term_matches(sym.qualified_name, terms):
                boost(fp, 4.0, f"symbol: {sym.qualified_name}")
                matched.setdefault(fp, []).append(entry)
            # Unqualified name (e.g. "stage")
            elif _term_matches(sym.name, terms):
                boost(fp, 3.0, f"symbol: {sym.name}")
                matched.setdefault(fp, []).append(entry)
            # Docstring first line
            elif sym.docstring and _term_matches(sym.docstring.split("\n")[0], terms):
                boost(fp, 1.5, f"docstring match: {sym.name}")
                matched.setdefault(fp, []).append(entry)

        # --- Filename stem matching ---
        for fi in index.indexed_files():
            stem = Path(fi.relative_path).stem
            if _term_matches(stem, terms):
                boost(fi.relative_path, 2.0, "filename match")

        # --- WorkspaceMemory boost ---
        if workspace_memory is not None:
            summary = workspace_memory.summary()
            for touched in summary.get("recently_edited_files", []):
                rel = _relativize(touched, root)
                if rel and rel in scores:
                    boost(rel, 1.5, "recently edited this session")
            for sym_name in summary.get("recently_created_symbols", []):
                for entry in index.lookup(sym_name):
                    if entry.relative_path in scores:
                        boost(
                            entry.relative_path,
                            1.0,
                            f"recently created symbol: {sym_name}",
                        )

        # --- PageRank boost ---
        # Re-rank term-matched files by their centrality in the import
        # graph, using the term scores as personalization weights so
        # high-relevance files attract more teleportation probability.
        # Mirrors Aider's repo-map approach. Non-fatal: a failure here
        # degrades silently to term-match-only ranking.
        if scores:
            try:
                personalization = {fp: max(sc, 1.0) for fp, sc in scores.items()}
                pr_scores = graph.pagerank(personalization=personalization)
                max_pr = max(pr_scores.values(), default=1.0) or 1.0
                for fp in list(scores.keys()):
                    pr = pr_scores.get(fp, 0.0)
                    if pr > 0:
                        # Add up to 1.5 points — enough to re-order equally
                        # relevant files, not enough to override term matches.
                        pr_boost = (pr / max_pr) * 1.5
                        boost(fp, pr_boost, f"pagerank={pr:.4f}")
            except Exception:
                logger.debug("PageRank boost failed; using term-match-only ranking.", exc_info=True)

        # --- Initial ranking ---
        initial = sorted(
            [
                RankedFile(
                    path=fp,
                    score=sc,
                    reasons=reasons.get(fp, []),
                    matched_symbols=matched.get(fp, []),
                )
                for fp, sc in scores.items()
            ],
            key=lambda r: (-r.score, r.path),
        )

        # --- Graph expansion ---
        if self.config.expansion_hops > 0 and initial:
            seed_files = {r.path for r in initial[: self.config.max_files // 2 + 1]}
            expansion = self._expand(seed_files, graph, scores)
            for fp, ex_score in expansion.items():
                if fp not in scores:
                    boost(fp, ex_score, "graph neighbor")
                    # Re-sort after adding expansions
            initial = sorted(
                [
                    RankedFile(
                        path=fp,
                        score=scores[fp],
                        reasons=reasons.get(fp, []),
                        matched_symbols=matched.get(fp, []),
                    )
                    for fp in scores
                ],
                key=lambda r: (-r.score, r.path),
            )

        return initial[: self.config.max_files]

    def _all_entries(self, index: RepositoryIndex) -> list[SymbolEntry]:
        """Return every SymbolEntry in the index."""
        result: list[SymbolEntry] = []
        for fi in index.indexed_files():
            result.extend(index.symbols_in_file(fi.relative_path))
        return result

    def _expand(
        self,
        seed_files: set[str],
        graph: RepositoryGraph,
        existing_scores: dict[str, float],
    ) -> dict[str, float]:
        """Return expansion scores for IMPORTS neighbors of *seed_files*."""
        expansion: dict[str, float] = {}
        for fp in seed_files:
            parent_score = existing_scores.get(fp, 0.0)
            # Files that import this file (reverse IMPORTS = dependents)
            for edge in graph.edges_in(fp, kind=EdgeKind.IMPORTS):
                neighbor = edge.source
                if neighbor not in existing_scores:
                    expansion[neighbor] = max(
                        expansion.get(neighbor, 0.0),
                        parent_score * 0.4,
                    )
            # Files that this file imports (forward IMPORTS = dependencies)
            for edge in graph.edges_out(fp, kind=EdgeKind.IMPORTS):
                neighbor = edge.target
                if neighbor not in existing_scores:
                    expansion[neighbor] = max(
                        expansion.get(neighbor, 0.0),
                        parent_score * 0.3,
                    )
        return expansion

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_candidates(
        self,
        candidates: list[RankedFile],
        index: RepositoryIndex,
        graph: RepositoryGraph,
        root: Path,
    ) -> list[str]:
        sections: list[str] = []
        budget = self.config.max_context_tokens
        included: set[str] = set()

        for ranked in candidates:
            if ranked.path in included:
                continue

            snippet = self._render_file(ranked, root)
            cost = _estimate_tokens(snippet)

            if cost > budget:
                continue

            sections.append(snippet)
            included.add(ranked.path)
            budget -= cost

            if budget <= 0:
                break

        return sections

    def _render_file(self, ranked: RankedFile, root: Path) -> str:
        fp = ranked.path
        full_path = root / fp

        try:
            text = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return self._format_workspace_file(fp, "(unreadable)")

        lines = text.splitlines()

        if len(lines) <= _FULL_CONTENT_THRESHOLD_LINES:
            body = "```\n" + text + "\n```"
        else:
            body = self._compress(fp, text, lines, ranked.matched_symbols)

        return self._format_workspace_file(fp, body)

    def _compress(
        self,
        rel_path: str,
        text: str,
        lines: list[str],
        matched_symbols: list[SymbolEntry],
    ) -> str:
        """Extract matched symbol bodies; summarise the rest."""
        kept: list[str] = []
        seen_ranges: set[tuple[int, int]] = set()

        # Sort by line_start so output is in source order
        sorted_symbols = sorted(
            matched_symbols,
            key=lambda e: e.symbol.line_start,
        )[:_MAX_SYMBOLS_PER_FILE]

        for entry in sorted_symbols:
            sym = entry.symbol
            s = max(0, sym.line_start - 1)  # convert 1-indexed → 0-indexed
            e = min(len(lines), sym.line_end)  # exclusive end
            key = (s, e)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            block = "\n".join(lines[s:e])
            kept.append("```\n" + block + "\n```")

        summary = (
            f"(compressed: {len(lines)} lines total; "
            f"showing {len(kept)} matched symbol(s) in full)"
        )

        # Preserve any TODO / FIXME comments regardless of match
        todo_lines = [
            line.strip() for line in lines if "TODO" in line or "FIXME" in line
        ]

        parts = [summary]
        if todo_lines:
            parts.append(
                "Outstanding notes:\n" + "\n".join(f"  {line}" for line in todo_lines)
            )
        parts.extend(kept)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def _relevant_cycles(
        self,
        graph: RepositoryGraph,
        file_paths: set[str],
    ) -> list[list[str]]:
        """Return only the import cycles that involve at least one selected file."""
        all_cycles = graph.detect_cycles()
        return [cycle for cycle in all_cycles if any(fp in file_paths for fp in cycle)]

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_workspace_file(path: str, content: str) -> str:
        return f"<workspace_file path={path!r}>\n{content}\n</workspace_file>"

    @staticmethod
    def _build_header(query: str, cycles: list[list[str]]) -> str:
        lines = [f"## Repository context for: {query!r}"]
        if cycles:
            names = "; ".join(
                "[" + ", ".join(fp.split("/")[-1] for fp in cycle) + "]"
                for cycle in cycles
            )
            lines.append(
                f"\nNote: the following file(s) are in circular import"
                f" group(s): {names}."
                "  Edits may require breaking the cycle."
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _relativize(path: str, root: Path) -> str | None:
    """Convert an absolute path to the relative path the index uses.

    Returns ``None`` if the path is not under *root*.
    """
    try:
        candidate = Path(path)
        if candidate.is_absolute():
            return str(candidate.relative_to(root))
        return path
    except ValueError:
        return None
