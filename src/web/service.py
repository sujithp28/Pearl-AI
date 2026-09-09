"""
WebIntelligenceService — orchestrates the full web pipeline.

Search → Fetch → Extract → Rank/Dedup → Bound → WebContext

All failures are contained: a single source failing never aborts the
whole pipeline. When all sources fail, WebContext is returned with 0
sources rather than raising an exception.
"""

from __future__ import annotations

import logging
import urllib.parse

from src.config.settings import Settings
from src.web.extract import ContentExtractor
from src.web.fetch import FetchError, PageFetcher
from src.web.models import WebContext, WebDocument, WebSource
from src.web.rank import rank_and_deduplicate
from src.web.search import WebSearcher, WebSearchError, create_searcher

logger = logging.getLogger(__name__)


class WebIntelligenceService:
    """
    Coordinates the web intelligence pipeline for a single query.

    Each call to `build_context` is stateless — no session state is
    preserved between calls, matching the tool's single-call contract.
    """

    def __init__(
        self,
        searcher: WebSearcher | None = None,
        fetcher: PageFetcher | None = None,
        extractor: ContentExtractor | None = None,
        max_results: int | None = None,
        evidence_chars: int | None = None,
    ) -> None:
        self._searcher = searcher or create_searcher(Settings.WEB_SEARCH_PROVIDER)
        self._fetcher = fetcher or PageFetcher(
            timeout=float(Settings.WEB_FETCH_TIMEOUT),
            max_bytes=Settings.WEB_MAX_RESPONSE_BYTES,
        )
        self._extractor = extractor or ContentExtractor()
        self._max_results = max_results if max_results is not None else Settings.WEB_MAX_RESULTS
        self._evidence_chars = evidence_chars if evidence_chars is not None else Settings.WEB_EVIDENCE_CHARS

    def build_context(self, query: str) -> WebContext:
        """
        Execute the full pipeline for `query` and return a WebContext.

        Never raises — all failures are surfaced as empty/partial context.
        """
        logger.info("Web intelligence: building context for query %r", query)

        # 1. Search
        search_results = self._search(query)

        if not search_results:
            logger.info("Web search returned no results for %r", query)
            return WebContext(query=query, sources=[], evidence=[], total_chars=0)

        # 2. Fetch + Extract (skip failures silently)
        docs: list[WebDocument] = []
        for sr in search_results[: self._max_results * 2]:  # fetch extra to survive skips
            doc = self._fetch_and_extract(sr.url)
            if doc is not None:
                docs.append(doc)
            if len(docs) >= self._max_results * 2:
                break

        if not docs:
            logger.warning("All fetch/extract attempts failed for query %r", query)
            # Return stub context from search snippets only.
            return self._context_from_snippets(query, search_results)

        # 3. Rank + Deduplicate
        ranked = rank_and_deduplicate(docs, query, max_results=self._max_results)

        # 4. Build bounded WebContext
        return self._assemble_context(query, ranked)

    def _search(self, query: str) -> list:
        try:
            return self._searcher.search(query, max_results=self._max_results * 2)
        except WebSearchError as exc:
            logger.warning("Web search failed: %s", exc)
            return []

    def _fetch_and_extract(self, url: str) -> WebDocument | None:
        try:
            raw = self._fetcher.fetch(url)
            doc = self._extractor.extract(raw)
            if not doc.text.strip():
                logger.debug("Empty text extracted from %s — skipping.", url)
                return None
            return doc
        except FetchError as exc:
            logger.info("Skipping %s: %s", url, exc)
            return None
        except Exception as exc:
            logger.warning("Unexpected error fetching %s: %s", url, exc)
            return None

    def _assemble_context(self, query: str, docs: list[WebDocument]) -> WebContext:
        sources: list[WebSource] = []
        evidence: list[str] = []
        total_chars = 0

        for doc in docs:
            domain = urllib.parse.urlparse(doc.url).netloc
            sources.append(WebSource(url=doc.url, title=doc.title, domain=domain))

            # Extract the most relevant excerpt.
            excerpt = self._extract_evidence(doc.text, query)
            evidence.append(excerpt)
            total_chars += len(excerpt)

        logger.info(
            "Web context built: %d sources, %d total chars.", len(sources), total_chars
        )
        return WebContext(
            query=query, sources=sources, evidence=evidence, total_chars=total_chars
        )

    def _context_from_snippets(self, query: str, results: list) -> WebContext:
        """Fallback: use search snippets when fetching completely failed."""
        sources: list[WebSource] = []
        evidence: list[str] = []
        total_chars = 0

        for sr in results[: self._max_results]:
            domain = urllib.parse.urlparse(sr.url).netloc
            sources.append(WebSource(url=sr.url, title=sr.title, domain=domain))
            snippet = sr.snippet[: self._evidence_chars] if sr.snippet else "(no snippet)"
            evidence.append(snippet)
            total_chars += len(snippet)

        return WebContext(
            query=query, sources=sources, evidence=evidence, total_chars=total_chars
        )

    def _extract_evidence(self, text: str, query: str) -> str:
        """
        Extract up to evidence_chars characters of text most relevant to the query.

        Simple heuristic: find the paragraph that contains the most query terms,
        then expand to fill the budget from that anchor point.
        """
        if len(text) <= self._evidence_chars:
            return text

        query_words = set(query.lower().split())
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        if not paragraphs:
            return text[: self._evidence_chars]

        best_para = max(
            paragraphs,
            key=lambda p: sum(1 for w in query_words if w in p.lower()),
        )
        anchor = text.find(best_para)

        if anchor == -1:
            return text[: self._evidence_chars]

        # Expand around the best paragraph to fill the budget.
        half = self._evidence_chars // 2
        start = max(0, anchor - half)
        end = min(len(text), start + self._evidence_chars)
        return text[start:end]
