"""Tests for src/tools/web_tools.py (@tool functions)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.web_tools import web_context, web_fetch, web_search
from src.web.fetch import FetchError, PrivateAddressError, UnsupportedSchemeError
from src.web.models import (
    RawPage,
    SearchResult,
    WebContext,
    WebSource,
)
from src.web.search import WebSearchError

# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


class TestWebSearch:
    def test_raises_on_empty_query(self) -> None:
        with pytest.raises(ValueError, match="query"):
            web_search("")

    def test_raises_on_whitespace_query(self) -> None:
        with pytest.raises(ValueError, match="query"):
            web_search("   ")

    def test_successful_search_returns_list_of_dicts(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            SearchResult(
                url="https://python.org",
                title="Python",
                snippet="Official site.",
                source="python.org",
            )
        ]
        with patch("src.tools.web_tools.create_searcher", return_value=mock_searcher):
            results = web_search("python", max_results=3)

        assert isinstance(results, list)
        assert results[0]["url"] == "https://python.org"
        assert results[0]["title"] == "Python"
        assert "snippet" in results[0]
        assert "source" in results[0]

    def test_search_provider_failure_returns_error_dict(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = WebSearchError("provider down")
        with patch("src.tools.web_tools.create_searcher", return_value=mock_searcher):
            results = web_search("python")

        assert isinstance(results, list)
        assert "error" in results[0]

    def test_empty_results_returned_as_empty_list(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        with patch("src.tools.web_tools.create_searcher", return_value=mock_searcher):
            results = web_search("nonexistent query xyz")

        assert results == []

    def test_max_results_capped_at_20(self) -> None:
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        with patch("src.tools.web_tools.create_searcher", return_value=mock_searcher):
            web_search("q", max_results=100)

        _, kwargs = mock_searcher.search.call_args
        assert kwargs.get("max_results", 100) <= 20


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------


class TestWebFetch:
    def test_raises_on_empty_url(self) -> None:
        with pytest.raises(ValueError, match="url"):
            web_fetch("")

    def test_successful_fetch_returns_dict_with_expected_keys(self) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = RawPage(
            url="https://example.com",
            content_type="text/html",
            body=b"<html><body><p>hello world</p></body></html>",
            original_url="https://example.com",
        )

        with patch("src.tools.web_tools._get_fetcher", return_value=mock_fetcher):
            result = web_fetch("https://example.com")

        assert "url" in result
        assert "title" in result
        assert "text" in result
        assert "chars" in result
        assert result["chars"] > 0

    def test_private_ip_rejection_returns_error_dict(self) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = PrivateAddressError("private IP blocked")
        with patch("src.tools.web_tools._get_fetcher", return_value=mock_fetcher):
            result = web_fetch("http://192.168.1.1/admin")

        assert "error" in result
        assert (
            "private" in result["error"].lower() or "blocked" in result["error"].lower()
        )

    def test_unsupported_scheme_returns_error_dict(self) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = UnsupportedSchemeError("file not allowed")
        with patch("src.tools.web_tools._get_fetcher", return_value=mock_fetcher):
            result = web_fetch("file:///etc/passwd")
        assert "error" in result
        assert isinstance(result["error"], str)

    def test_fetch_timeout_returns_error_dict(self) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = FetchError("timed out")
        with patch("src.tools.web_tools._get_fetcher", return_value=mock_fetcher):
            result = web_fetch("https://slow.example.com/")

        assert "error" in result

    def test_url_preserved_in_response(self) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = RawPage(
            url="https://specific.example.com/path",
            content_type="text/html",
            body=b"<html><body>text</body></html>",
            original_url="https://specific.example.com/path",
        )
        with patch("src.tools.web_tools._get_fetcher", return_value=mock_fetcher):
            result = web_fetch("https://specific.example.com/path")

        assert result["url"] == "https://specific.example.com/path"


# ---------------------------------------------------------------------------
# web_context
# ---------------------------------------------------------------------------


class TestWebContext:
    def test_raises_on_empty_query(self) -> None:
        with pytest.raises(ValueError, match="query"):
            web_context("")

    def test_returns_string(self) -> None:
        mock_svc = MagicMock()
        mock_svc.build_context.return_value = WebContext(
            query="python version",
            sources=[
                WebSource(url="https://python.org", title="Python", domain="python.org")
            ],
            evidence=["Python 3.12 was released."],
            total_chars=26,
        )
        with patch("src.tools.web_tools._get_service", return_value=mock_svc):
            result = web_context("python version")

        assert isinstance(result, str)
        assert "python version" in result
        assert "Python" in result

    def test_empty_results_returns_no_results_message(self) -> None:
        mock_svc = MagicMock()
        mock_svc.build_context.return_value = WebContext(
            query="xyzzy nonexistent",
            sources=[],
            evidence=[],
            total_chars=0,
        )
        with patch("src.tools.web_tools._get_service", return_value=mock_svc):
            result = web_context("xyzzy nonexistent")

        assert "No results found" in result

    def test_source_url_included_in_output(self) -> None:
        mock_svc = MagicMock()
        mock_svc.build_context.return_value = WebContext(
            query="q",
            sources=[
                WebSource(
                    url="https://target.com/doc", title="Doc", domain="target.com"
                )
            ],
            evidence=["Relevant content here."],
            total_chars=22,
        )
        with patch("src.tools.web_tools._get_service", return_value=mock_svc):
            result = web_context("q")

        assert "https://target.com/doc" in result

    def test_citation_structure_present(self) -> None:
        mock_svc = MagicMock()
        mock_svc.build_context.return_value = WebContext(
            query="kubernetes version",
            sources=[
                WebSource(
                    url="https://kubernetes.io/",
                    title="Kubernetes",
                    domain="kubernetes.io",
                ),
            ],
            evidence=["Kubernetes 1.30 released."],
            total_chars=25,
        )
        with patch("src.tools.web_tools._get_service", return_value=mock_svc):
            result = web_context("kubernetes version")

        assert "[1]" in result
        assert "URL:" in result
        assert "Kubernetes" in result

    def test_provider_failure_returns_graceful_string(self) -> None:
        mock_svc = MagicMock()
        mock_svc.build_context.return_value = WebContext(
            query="q", sources=[], evidence=[], total_chars=0
        )
        with patch("src.tools.web_tools._get_service", return_value=mock_svc):
            result = web_context("q")

        assert isinstance(result, str)
        assert len(result) > 0
