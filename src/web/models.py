"""
Data models for the Web Intelligence subsystem.

All models are plain dataclasses — no LLM, no HTTP, no I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SearchResult:
    """A single result returned by a web search provider."""

    url: str
    title: str
    snippet: str
    source: str  # domain, e.g. "docs.python.org"


@dataclass
class RawPage:
    """Raw HTTP response for a URL before content extraction."""

    url: str          # final URL after redirects
    content_type: str
    body: bytes
    original_url: str  # the URL that was requested (before redirects)


@dataclass
class WebDocument:
    """Extracted, human-readable content from a web page."""

    url: str
    title: str
    text: str                           # clean prose, no HTML
    headings: list[str] = field(default_factory=list)
    retrieved_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    relevance_score: float = 0.0        # set by rank.py


@dataclass
class WebSource:
    """Compact source reference preserved in WebContext."""

    url: str
    title: str
    domain: str


@dataclass
class WebContext:
    """
    Bounded, source-attributed evidence ready to send to the LLM.

    `evidence` is a list of formatted blocks — one per source — each
    containing the URL, title, and a relevance-trimmed excerpt. The
    total character count is always <= max_results * evidence_chars_per_source.
    """

    query: str
    sources: list[WebSource]
    evidence: list[str]      # formatted per-source blocks, same order as sources
    total_chars: int

    def format_for_llm(self) -> str:
        """
        Return a single string suitable for inclusion in an LLM prompt.

        Format:
            Web search results for: <query>

            [1] <title>
            URL: <url>
            ---
            <evidence>

            [2] ...
        """
        if not self.sources:
            return f"Web search results for: {self.query}\n\nNo results found."

        lines: list[str] = [f"Web search results for: {self.query}\n"]
        for i, (source, evidence) in enumerate(
            zip(self.sources, self.evidence), start=1
        ):
            lines.append(f"[{i}] {source.title}")
            lines.append(f"URL: {source.url}")
            lines.append("---")
            lines.append(evidence)
            lines.append("")

        return "\n".join(lines).rstrip()
