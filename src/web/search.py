"""
Web search abstraction and provider implementations.

The WebSearcher protocol is the only interface callers depend on.
Implementations are swapped by setting PEARL_WEB_SEARCH_PROVIDER.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Protocol

import httpx

from src.web.models import SearchResult

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT = 10.0  # seconds


class WebSearchError(Exception):
    """Raised when the search provider fails completely."""


class WebSearcher(Protocol):
    def search(self, query: str, max_results: int) -> list[SearchResult]:
        """Return up to max_results SearchResults for query."""
        ...


class DuckDuckGoSearcher:
    """
    Search via DuckDuckGo's HTML lite endpoint — no API key required.

    Parses the HTML response to extract result URLs, titles, and snippets.
    This approach is intentionally simple and robust; DuckDuckGo's lite
    HTML is stable and deliberately machine-readable.
    """

    _SEARCH_URL = "https://html.duckduckgo.com/html/"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; Pearl-AI/1.2; +https://github.com/sujithp28/Pearl-AI)"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = httpx.post(
                self._SEARCH_URL,
                data={"q": query, "kl": "us-en"},
                headers=self._HEADERS,
                timeout=_SEARCH_TIMEOUT,
                follow_redirects=True,
            )
            resp.raise_for_status()
            return self._parse(resp.text, max_results)
        except httpx.TimeoutException as exc:
            raise WebSearchError(f"DuckDuckGo search timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise WebSearchError(f"DuckDuckGo search failed: {exc}") from exc

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise WebSearchError("beautifulsoup4 is required for web search") from exc

        soup = BeautifulSoup(html, "html.parser")
        results: list[SearchResult] = []

        for result_div in soup.select(".result"):
            if len(results) >= max_results:
                break

            title_tag = result_div.select_one(".result__a")
            snippet_tag = result_div.select_one(".result__snippet")

            if not title_tag:
                continue

            raw_href = title_tag.get("href", "")
            url = self._extract_url(str(raw_href))
            if not url:
                continue

            title = title_tag.get_text(strip=True)
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            domain = urllib.parse.urlparse(url).netloc

            results.append(
                SearchResult(
                    url=url,
                    title=title or domain,
                    snippet=snippet,
                    source=domain,
                )
            )

        return results

    @staticmethod
    def _extract_url(href: str) -> str:
        """
        DuckDuckGo wraps result URLs in a redirect.
        Extract the actual destination URL from the uddg= parameter.
        """
        if href.startswith("//duckduckgo.com/l/"):
            parsed = urllib.parse.urlparse("https:" + href)
            params = urllib.parse.parse_qs(parsed.query)
            uddg = params.get("uddg", [""])[0]
            if uddg:
                return urllib.parse.unquote(uddg)
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return ""


class NullSearcher:
    """
    A no-op searcher that always returns empty results.
    Used when WEB_SEARCH_PROVIDER=none — disables web search
    without removing the tool from the registry.
    """

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return []


def create_searcher(provider: str) -> WebSearcher:
    normalized = provider.strip().lower()
    if normalized == "duckduckgo":
        return DuckDuckGoSearcher()
    if normalized == "none":
        return NullSearcher()
    raise ValueError(
        f"Unknown web search provider: {provider!r}. Supported: 'duckduckgo', 'none'."
    )
