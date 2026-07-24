"""
Context compression and smart retrieval for Pearl.

`ContextManager` ranks the repository's files by relevance to a
query, and `ContextBuilder` turns that ranking into a single,
token-budgeted, LLM-ready context string — full snippets for small
or highly-relevant files, compressed summaries (matched symbols kept
in full, everything else summarized) for large ones.

Reuses Pearl's existing components instead of introducing a second
indexing system:
- `RepositoryIndex` (`repo_tools.get_repository_index`) is the sole
  source of "what files/symbols/imports exist" — this module never
  walks the filesystem or re-parses Python itself.
- `WorkspaceMemory` (optional) supplies session signals — recently
  edited files, recently created symbols — as an extra ranking boost.
- `SymbolEditor` extracts a matched symbol's exact source (formatting
  preserved) when compressing a large file.

The resulting context string is deliberately just a plain `str`:
`Planner.plan(..., workspace_context=...)` already accepts one
without knowing or caring what produced it (see `planner.py`), so
there is nothing to hard-code — `Planner` never imports this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.memory.workspace_memory import WorkspaceMemory
from src.tools.repo_tools import RepositoryIndex, get_repository_index
from src.tools.symbol_editor import (
    SymbolEditor,
    SymbolNotFoundError,
    UnsupportedLanguageError,
)

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Common function words filtered out of queries before matching —
# without this, a word like "the" (>= 3 chars) would substring-match
# unrelated filenames like "other.py".
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
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
    }
)

# Rough estimate: ~4 characters per token. No tokenizer dependency is
# introduced for this — it only needs to be good enough to budget by.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """
    Estimate how many LLM tokens `text` costs.
    """

    if not text:
        return 0

    return max(1, len(text) // _CHARS_PER_TOKEN)


def _tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(text)} - _STOPWORDS


def _matches(name: str, terms: set[str]) -> bool:
    if not name or not terms or len(name) < 3:
        return False

    lowered = name.lower()

    return any(
        len(term) >= 3 and (term in lowered or lowered in term) for term in terms
    )


def _relativize(path: str, root: Path) -> str:
    """
    Normalize a possibly-absolute path (as a tool call, and so
    `WorkspaceMemory`, may have recorded it) to the root-relative
    form `RepositoryIndex` keys files by. Returns `path` unchanged if
    it isn't under `root`.
    """

    candidate = Path(path)

    if not candidate.is_absolute():
        return path

    try:
        return str(candidate.relative_to(root))
    except ValueError:
        return path


@dataclass(slots=True)
class ContextConfig:
    """
    Budgets and thresholds `ContextManager`/`ContextBuilder` respect.
    """

    max_context_tokens: int = 4000
    max_files: int = 10
    compression_threshold: int = 200  # lines; longer files get compressed


@dataclass(slots=True)
class RankedFile:
    """
    One file's relevance score, with the signals that produced it
    (useful for debugging/tests, not just the number itself).
    """

    path: str
    score: float
    reasons: list[str] = field(default_factory=list)


class ContextManager:
    """
    Ranks a repository's files by relevance to a query, using the
    existing `RepositoryIndex` (symbol names, filenames) and,
    optionally, `WorkspaceMemory` signals (recently edited files,
    recently created symbols) as an extra boost.
    """

    def __init__(self, config: ContextConfig | None = None) -> None:
        self.config = config or ContextConfig()

    def rank_files(
        self,
        query: str,
        path: str = ".",
        workspace_memory: WorkspaceMemory | None = None,
        index: RepositoryIndex | None = None,
    ) -> list[RankedFile]:
        """
        Rank every file in the (existing) repository index by
        relevance to `query`, highest first, capped at
        `config.max_files`. Files that RepositoryIndex already
        excludes (generated/vendor/cache directories, non-source
        files) never appear here.
        """

        index = index if index is not None else get_repository_index(path)
        terms = _tokenize(query)

        scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}

        def boost(file: str, amount: float, reason: str) -> None:
            scores[file] = scores.get(file, 0.0) + amount
            reasons.setdefault(file, []).append(reason)

        for symbol_name, locations in index.symbols.items():
            if _matches(symbol_name, terms):
                for location in locations:
                    boost(location["file"], 3.0, f"symbol match: {symbol_name}")

        for file in index.files:
            if _matches(Path(file).stem, terms):
                boost(file, 2.0, "filename match")

        if workspace_memory is not None:
            summary = workspace_memory.summary()

            for touched in summary["recently_edited_files"]:
                rel = _relativize(touched, index.root)
                if rel in index.files:
                    boost(rel, 1.5, "recently edited this session")

            recent_symbols = set(summary["recently_created_symbols"])

            for symbol_name, locations in index.symbols.items():
                if symbol_name in recent_symbols:
                    for location in locations:
                        boost(
                            location["file"],
                            1.0,
                            f"recently created symbol: {symbol_name}",
                        )

        # `scores` is keyed by file, so this is already deduplicated —
        # no file can appear twice in the ranking.
        ranked = [
            RankedFile(path=file, score=score, reasons=reasons[file])
            for file, score in scores.items()
        ]
        ranked.sort(key=lambda r: (-r.score, r.path))

        return ranked[: self.config.max_files]


class ContextBuilder:
    """
    Builds a single, token-budgeted, LLM-ready context string: an
    optional workspace summary followed by one section per relevant
    file (snippet, imports, public API), most relevant first,
    compressing files over `config.compression_threshold` lines and
    stopping once the token budget is spent.
    """

    def __init__(
        self,
        config: ContextConfig | None = None,
        manager: ContextManager | None = None,
    ) -> None:
        self.config = config or ContextConfig()
        self.manager = manager or ContextManager(self.config)

    def build(
        self,
        query: str,
        path: str = ".",
        workspace_memory: WorkspaceMemory | None = None,
        include_workspace_summary: bool = True,
    ) -> str:
        """
        Return the assembled context string for `query`. Returns ""
        if nothing relevant was found (or the budget is exhausted
        before anything fits).
        """

        index = get_repository_index(path)
        ranked = self.manager.rank_files(query, path, workspace_memory, index=index)

        sections: list[str] = []
        included: set[str] = set()
        budget = self.config.max_context_tokens

        if include_workspace_summary and workspace_memory is not None:
            workspace_summary = workspace_memory.generate_context()

            if workspace_summary:
                section = "## Workspace summary\n\n" + workspace_summary
                cost = estimate_tokens(section)

                if cost <= budget:
                    sections.append(section)
                    budget -= cost

        for ranked_file in ranked:
            if ranked_file.path in included:
                continue

            snippet = self._render_file(ranked_file.path, index, query)
            cost = estimate_tokens(snippet)

            if cost > budget:
                continue

            sections.append(snippet)
            included.add(ranked_file.path)
            budget -= cost

            if budget <= 0:
                break

        return "\n\n".join(sections)

    def _render_file(self, rel_path: str, index: RepositoryIndex, query: str) -> str:
        full_path = index.root / rel_path

        try:
            text = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return f"## {rel_path}\n\n(unreadable)"

        lines = text.splitlines()
        imports = index.imports.get(rel_path, [])
        symbols = [
            {"name": name, **location}
            for name, locations in index.symbols.items()
            for location in locations
            if location["file"] == rel_path
        ]

        header_parts = [f"## {rel_path}"]

        if imports:
            header_parts.append("Imports: " + ", ".join(imports))

        public_symbols = [
            symbol["name"] for symbol in symbols if not symbol["name"].startswith("_")
        ]

        if public_symbols:
            header_parts.append("Public API: " + ", ".join(public_symbols))

        header = "\n\n".join(header_parts)

        if len(lines) <= self.config.compression_threshold:
            body = "```\n" + text + "\n```"
        else:
            body = self._compress(rel_path, text, symbols, query)

        return header + "\n\n" + body

    def _compress(
        self,
        rel_path: str,
        text: str,
        symbols: list[dict[str, Any]],
        query: str,
    ) -> str:
        """
        Compress a large file: keep symbols matching `query` in full
        (via `SymbolEditor`, so formatting/decorators are preserved),
        summarize everything else, and preserve any TODO/FIXME
        comment lines regardless of match.
        """

        terms = _tokenize(query)
        matched = [symbol for symbol in symbols if _matches(symbol["name"], terms)]

        kept_blocks: list[str] = []

        try:
            editor = SymbolEditor(rel_path, text)

            for symbol in matched:
                try:
                    if symbol["type"] == "class":
                        location = editor.find_class(symbol["name"])
                    else:
                        location = editor.find_function(symbol["name"])

                    kept_blocks.append(location.source)
                except SymbolNotFoundError:
                    continue
        except UnsupportedLanguageError:
            pass

        todo_lines = [
            line.strip()
            for line in text.splitlines()
            if "TODO" in line or "FIXME" in line
        ]

        summary_lines = [
            f"(compressed: {len(text.splitlines())} lines total; "
            f"showing {len(kept_blocks)} matched symbol(s) in full)"
        ]

        if todo_lines:
            summary_lines.append("Outstanding notes:")
            summary_lines.extend(f"  {line}" for line in todo_lines)

        parts = ["\n".join(summary_lines)]
        parts.extend("```\n" + block + "```" for block in kept_blocks)

        return "\n\n".join(parts)
