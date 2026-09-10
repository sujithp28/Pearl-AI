"""Tests for src/web/rank.py."""

from __future__ import annotations

from src.web.models import WebDocument
from src.web.rank import _jaccard_similarity, _normalise_url, rank_and_deduplicate


def _doc(url: str, title: str, text: str) -> WebDocument:
    return WebDocument(url=url, title=title, text=text)


class TestNormaliseUrl:
    def test_strips_query_string(self) -> None:
        assert _normalise_url("https://a.com/p?tracking=123") == "https://a.com/p"

    def test_strips_fragment(self) -> None:
        assert _normalise_url("https://a.com/p#section") == "https://a.com/p"

    def test_preserves_path(self) -> None:
        assert "article" in _normalise_url("https://a.com/article")

    def test_same_url_equal_after_normalise(self) -> None:
        a = _normalise_url("https://a.com/p?x=1")
        b = _normalise_url("https://a.com/p?y=2")
        assert a == b


class TestJaccardSimilarity:
    def test_identical_texts_give_1(self) -> None:
        assert _jaccard_similarity("hello world", "hello world") == 1.0

    def test_empty_texts_give_0(self) -> None:
        assert _jaccard_similarity("", "") == 0.0

    def test_disjoint_texts_give_0(self) -> None:
        assert _jaccard_similarity("apple banana", "orange pear") == 0.0

    def test_partial_overlap(self) -> None:
        sim = _jaccard_similarity("hello world foo", "hello world bar")
        assert 0.0 < sim < 1.0


class TestRankAndDeduplicate:
    def test_empty_input_returns_empty(self) -> None:
        assert rank_and_deduplicate([], "query") == []

    def test_max_results_honoured(self) -> None:
        docs = [
            _doc(f"https://a{i}.com/", f"Title {i}", f"content {i}") for i in range(10)
        ]
        result = rank_and_deduplicate(docs, "content", max_results=3)
        assert len(result) <= 3

    def test_duplicate_urls_deduplicated(self) -> None:
        doc1 = _doc("https://a.com/page?x=1", "A", "some content about python")
        doc2 = _doc("https://a.com/page?x=2", "A copy", "some content about python")
        result = rank_and_deduplicate([doc1, doc2], "python", max_results=5)
        # Both normalize to same URL — only one survives
        assert len(result) == 1

    def test_near_duplicate_text_deduplicated(self) -> None:
        text = "Python is a programming language " * 10
        doc1 = _doc("https://a.com/1", "A", text)
        doc2 = _doc("https://b.com/2", "B", text)  # Same text, different URL
        result = rank_and_deduplicate([doc1, doc2], "python programming", max_results=5)
        assert len(result) == 1

    def test_relevant_doc_ranked_higher(self) -> None:
        relevant = _doc(
            "https://r.com/", "Relevant", "python 3.12 release notes features changelog"
        )
        irrelevant = _doc(
            "https://i.com/", "Irrelevant", "cooking recipes pasta carbonara"
        )
        result = rank_and_deduplicate(
            [irrelevant, relevant], "python release", max_results=5
        )
        assert result[0].url == "https://r.com/"

    def test_relevance_scores_set(self) -> None:
        docs = [_doc("https://a.com/", "A", "python programming language")]
        result = rank_and_deduplicate(docs, "python", max_results=5)
        assert result[0].relevance_score >= 0.0

    def test_distinct_docs_all_preserved_under_limit(self) -> None:
        docs = [
            _doc(f"https://site{i}.com/", f"Site {i}", f"unique content site {i} xyz")
            for i in range(3)
        ]
        result = rank_and_deduplicate(docs, "content", max_results=5)
        assert len(result) == 3
