"""Tests for src/web/models.py."""

from __future__ import annotations

from src.web.models import SearchResult, WebContext, WebDocument, WebSource


class TestWebContextFormatForLlm:
    def test_empty_sources_returns_no_results_message(self) -> None:
        ctx = WebContext(query="test", sources=[], evidence=[], total_chars=0)
        out = ctx.format_for_llm()
        assert "No results found" in out
        assert "test" in out

    def test_single_source_formatted_correctly(self) -> None:
        ctx = WebContext(
            query="python version",
            sources=[WebSource(url="https://example.com", title="Example", domain="example.com")],
            evidence=["Python 3.12 was released."],
            total_chars=26,
        )
        out = ctx.format_for_llm()
        assert "python version" in out
        assert "Example" in out
        assert "https://example.com" in out
        assert "Python 3.12 was released." in out
        assert "[1]" in out

    def test_multiple_sources_numbered(self) -> None:
        ctx = WebContext(
            query="q",
            sources=[
                WebSource(url="https://a.com", title="A", domain="a.com"),
                WebSource(url="https://b.com", title="B", domain="b.com"),
            ],
            evidence=["Evidence A", "Evidence B"],
            total_chars=20,
        )
        out = ctx.format_for_llm()
        assert "[1]" in out
        assert "[2]" in out
        assert "Evidence A" in out
        assert "Evidence B" in out

    def test_total_chars_reflects_evidence(self) -> None:
        ev = "hello world"
        ctx = WebContext(
            query="q",
            sources=[WebSource(url="https://a.com", title="A", domain="a.com")],
            evidence=[ev],
            total_chars=len(ev),
        )
        assert ctx.total_chars == 11


class TestSearchResult:
    def test_fields_preserved(self) -> None:
        sr = SearchResult(
            url="https://example.com",
            title="Example",
            snippet="A snippet.",
            source="example.com",
        )
        assert sr.url == "https://example.com"
        assert sr.source == "example.com"


class TestWebDocument:
    def test_default_relevance_score_is_zero(self) -> None:
        doc = WebDocument(url="https://x.com", title="X", text="hello")
        assert doc.relevance_score == 0.0

    def test_headings_default_to_empty_list(self) -> None:
        doc = WebDocument(url="https://x.com", title="X", text="hello")
        assert doc.headings == []
