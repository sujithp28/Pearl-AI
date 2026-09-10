"""Tests for src/web/search.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.web.search import (
    DuckDuckGoSearcher,
    NullSearcher,
    WebSearchError,
    create_searcher,
)


class TestNullSearcher:
    def test_returns_empty_list(self) -> None:
        assert NullSearcher().search("anything", max_results=5) == []

    def test_respects_any_max_results(self) -> None:
        assert NullSearcher().search("q", max_results=100) == []


class TestCreateSearcher:
    def test_duckduckgo_provider(self) -> None:
        assert isinstance(create_searcher("duckduckgo"), DuckDuckGoSearcher)

    def test_none_provider(self) -> None:
        assert isinstance(create_searcher("none"), NullSearcher)

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown web search provider"):
            create_searcher("bing")

    def test_case_insensitive(self) -> None:
        assert isinstance(create_searcher("DuckDuckGo"), DuckDuckGoSearcher)


class TestDuckDuckGoSearcher:
    _SAMPLE_HTML = """
    <html><body>
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2F">
        Python.org</a>
      <div class="result__snippet">Official Python website.</div>
    </div>
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F">
        Docs</a>
      <div class="result__snippet">Python documentation.</div>
    </div>
    </body></html>
    """

    def test_parses_results_correctly(self) -> None:
        searcher = DuckDuckGoSearcher()
        results = searcher._parse(self._SAMPLE_HTML, max_results=5)
        assert len(results) == 2
        assert results[0].url == "https://python.org/"
        assert results[0].title == "Python.org"
        assert "Official Python" in results[0].snippet
        assert results[0].source == "python.org"

    def test_max_results_honoured(self) -> None:
        searcher = DuckDuckGoSearcher()
        results = searcher._parse(self._SAMPLE_HTML, max_results=1)
        assert len(results) == 1

    def test_empty_html_returns_empty_list(self) -> None:
        searcher = DuckDuckGoSearcher()
        assert searcher._parse("<html><body></body></html>", max_results=5) == []

    def test_timeout_raises_web_search_error(self) -> None:
        import httpx

        searcher = DuckDuckGoSearcher()
        with patch(
            "src.web.search.httpx.post", side_effect=httpx.TimeoutException("timed out")
        ):
            with pytest.raises(WebSearchError, match="timed out"):
                searcher.search("python", max_results=5)

    def test_http_error_raises_web_search_error(self) -> None:
        import httpx

        searcher = DuckDuckGoSearcher()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 error", request=MagicMock(), response=mock_resp
        )
        with patch("src.web.search.httpx.post", return_value=mock_resp):
            with pytest.raises(WebSearchError):
                searcher.search("python", max_results=5)

    def test_successful_network_call(self) -> None:
        searcher = DuckDuckGoSearcher()
        mock_resp = MagicMock()
        mock_resp.text = self._SAMPLE_HTML
        mock_resp.raise_for_status = MagicMock()

        with patch("src.web.search.httpx.post", return_value=mock_resp):
            results = searcher.search("python", max_results=5)

        assert len(results) == 2
        assert results[0].url == "https://python.org/"

    def test_extract_url_from_duckduckgo_redirect(self) -> None:
        searcher = DuckDuckGoSearcher()
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2F"
        assert searcher._extract_url(href) == "https://python.org/"

    def test_extract_url_direct_https(self) -> None:
        searcher = DuckDuckGoSearcher()
        assert searcher._extract_url("https://example.com") == "https://example.com"

    def test_extract_url_unknown_returns_empty(self) -> None:
        searcher = DuckDuckGoSearcher()
        assert searcher._extract_url("ftp://bad.com") == ""
