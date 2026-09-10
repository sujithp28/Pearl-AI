"""Tests for src/web/service.py (WebIntelligenceService)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.web.models import RawPage, SearchResult, WebDocument
from src.web.search import WebSearchError
from src.web.service import WebIntelligenceService


def _make_search_result(n: int = 1) -> SearchResult:
    return SearchResult(
        url=f"https://example{n}.com/article",
        title=f"Example Article {n}",
        snippet=f"A great article about topic {n}.",
        source=f"example{n}.com",
    )


def _make_web_document(n: int = 1) -> WebDocument:
    return WebDocument(
        url=f"https://example{n}.com/article",
        title=f"Example Article {n}",
        text=f"This is the full text of article {n} about python programming language.",
    )


def _make_raw_page(n: int = 1) -> RawPage:
    return RawPage(
        url=f"https://example{n}.com/article",
        content_type="text/html",
        body=b"<html><body><p>article text</p></body></html>",
        original_url=f"https://example{n}.com/article",
    )


class TestWebIntelligenceServiceBuildContext:
    def test_empty_query_returns_empty_context(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        svc = WebIntelligenceService(searcher=mock_searcher)
        ctx = svc.build_context("")
        assert ctx.query == ""
        assert len(ctx.sources) == 0

    def test_returns_empty_context_when_search_fails(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = WebSearchError("no results")
        svc = WebIntelligenceService(searcher=mock_searcher)

        ctx = svc.build_context("python latest version")
        assert len(ctx.sources) == 0
        assert ctx.query == "python latest version"

    def test_returns_empty_context_when_search_returns_nothing(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        svc = WebIntelligenceService(searcher=mock_searcher)

        ctx = svc.build_context("python latest version")
        assert len(ctx.sources) == 0

    def test_returns_snippet_context_when_all_fetches_fail(self) -> None:
        from src.web.fetch import FetchError

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [_make_search_result(1)]

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = FetchError("timeout")

        svc = WebIntelligenceService(searcher=mock_searcher, fetcher=mock_fetcher)
        ctx = svc.build_context("python")
        # Falls back to snippet context — sources from search snippets
        assert len(ctx.sources) == 1
        assert "example1.com" in ctx.sources[0].url

    def test_successful_pipeline_returns_context_with_sources(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            _make_search_result(1),
            _make_search_result(2),
        ]

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = _make_raw_page(1)

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = _make_web_document(1)

        svc = WebIntelligenceService(
            searcher=mock_searcher,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            max_results=3,
            evidence_chars=500,
        )
        ctx = svc.build_context("python")
        assert len(ctx.sources) >= 1
        assert len(ctx.evidence) >= 1
        assert ctx.total_chars > 0

    def test_single_failing_source_does_not_abort_pipeline(self) -> None:
        from src.web.fetch import FetchError

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            _make_search_result(1),  # will fail
            _make_search_result(2),  # will succeed
        ]

        call_count = {"n": 0}

        def side_effect(url):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise FetchError("first one fails")
            return _make_raw_page(2)

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = side_effect

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = _make_web_document(2)

        svc = WebIntelligenceService(
            searcher=mock_searcher,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            max_results=3,
            evidence_chars=500,
        )
        ctx = svc.build_context("python")
        # Should have at least the second source
        assert len(ctx.sources) >= 1

    def test_max_results_limits_output(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [_make_search_result(i) for i in range(10)]

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = lambda url: _make_raw_page(1)

        mock_extractor = MagicMock()
        mock_extractor.extract.side_effect = lambda raw: _make_web_document(1)

        svc = WebIntelligenceService(
            searcher=mock_searcher,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            max_results=2,
            evidence_chars=500,
        )
        ctx = svc.build_context("python")
        assert len(ctx.sources) <= 2

    def test_evidence_chars_limits_per_source_excerpt(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [_make_search_result(1)]

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = _make_raw_page(1)

        long_text = "python " * 2000  # >> evidence_chars
        long_doc = WebDocument(
            url="https://example1.com/article",
            title="Long Article",
            text=long_text,
        )

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = long_doc

        svc = WebIntelligenceService(
            searcher=mock_searcher,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            max_results=3,
            evidence_chars=100,
        )
        ctx = svc.build_context("python")
        assert all(len(ev) <= 100 for ev in ctx.evidence)

    def test_context_format_for_llm_includes_sources(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [_make_search_result(1)]

        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = _make_raw_page(1)

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = _make_web_document(1)

        svc = WebIntelligenceService(
            searcher=mock_searcher,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            max_results=3,
            evidence_chars=500,
        )
        ctx = svc.build_context("python")
        formatted = ctx.format_for_llm()
        assert "python" in formatted
        assert "URL:" in formatted
