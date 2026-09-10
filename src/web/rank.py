"""
Relevance ranking and deduplication for WebDocuments.

Ranking uses word-overlap TF-IDF (no external NLP dependencies).
Deduplication normalises URLs and checks text similarity.
"""

from __future__ import annotations

import math
import re
import urllib.parse
from collections import Counter


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())


def _tf_idf_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """
    Simple TF-IDF-like relevance: sum of (tf * log(1 + query_count)) for each
    query term found in the document. Good enough for ranking a handful of pages.
    """
    if not doc_tokens or not query_tokens:
        return 0.0

    doc_freq = Counter(doc_tokens)
    doc_len = len(doc_tokens)
    score = 0.0

    for token in set(query_tokens):
        tf = doc_freq.get(token, 0) / doc_len
        idf = math.log(1 + query_tokens.count(token))
        score += tf * idf

    return score


def _title_boost(query_tokens: list[str], title: str) -> float:
    """Extra weight when query terms appear in the document title."""
    title_tokens = _tokenize(title)
    hits = sum(1 for t in set(query_tokens) if t in title_tokens)
    return hits * 0.5


def _normalise_url(url: str) -> str:
    """Strip tracking parameters and fragment so near-duplicate URLs are equal."""
    parsed = urllib.parse.urlparse(url)
    # Strip query string entirely for dedup (tracking IDs, sessions, etc.)
    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))


def _jaccard_similarity(a: str, b: str) -> float:
    """Word-set Jaccard similarity of two text snippets."""
    set_a = set(_tokenize(a[:400]))
    set_b = set(_tokenize(b[:400]))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def rank_and_deduplicate(
    docs: list,  # list[WebDocument]
    query: str,
    max_results: int = 5,
) -> list:  # list[WebDocument]
    """
    Score, deduplicate, and return up to max_results WebDocuments.

    Deduplication:
    1. Normalised URL equality → keep higher-scored copy.
    2. Text Jaccard similarity ≥ 0.8 → keep higher-scored copy.

    Scoring:
    TF-IDF on body text + title boost.
    """
    if not docs:
        return []

    query_tokens = _tokenize(query)

    # Score all documents.
    scored: list[tuple[float, object]] = []
    for doc in docs:
        body_tokens = _tokenize(doc.text[:4000])
        score = _tf_idf_score(query_tokens, body_tokens)
        score += _title_boost(query_tokens, doc.title)
        doc.relevance_score = score
        scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate.
    seen_urls: set[str] = set()
    kept: list = []

    for _, doc in scored:
        if len(kept) >= max_results:
            break

        norm = _normalise_url(doc.url)
        if norm in seen_urls:
            continue

        # Near-duplicate text check against already-kept documents.
        is_dup = False
        for kept_doc in kept:
            if _jaccard_similarity(doc.text, kept_doc.text) >= 0.8:
                is_dup = True
                break

        if is_dup:
            continue

        seen_urls.add(norm)
        kept.append(doc)

    return kept
