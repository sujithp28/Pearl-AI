"""
Web Intelligence tools (M7).

Three tools are exposed to the planner:

  web_search(query)
    Returns a list of SearchResult dicts. Use this when you need to
    discover URLs before deciding which pages to fetch.

  web_fetch(url)
    Fetches and extracts a single URL. Use after web_search when you
    need the full text of a specific page.

  web_context(query)
    One-shot pipeline: search → fetch → extract → rank → bounded context.
    This is the preferred tool for answering questions that require
    current external information. Returns a formatted string with source
    citations ready to include in an LLM prompt.

Security:
  All three tools use PageFetcher, which rejects private/loopback IPs,
  file:// and other unsafe schemes, oversized responses, and timeouts.
  Failures are surfaced as descriptive error strings — they never crash
  the tool call.
"""

from __future__ import annotations

import logging

from src.config.settings import Settings
from src.tools.metadata import tool
from src.web.extract import ContentExtractor
from src.web.fetch import FetchError, PageFetcher
from src.web.models import WebContext
from src.web.search import WebSearchError, create_searcher
from src.web.service import WebIntelligenceService

logger = logging.getLogger(__name__)

# Module-level singletons (created lazily on first use).
_service: WebIntelligenceService | None = None
_fetcher: PageFetcher | None = None
_extractor: ContentExtractor | None = None


def _get_service() -> WebIntelligenceService:
    global _service
    if _service is None:
        _service = WebIntelligenceService()
    return _service


def _get_fetcher() -> PageFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = PageFetcher(
            timeout=float(Settings.WEB_FETCH_TIMEOUT),
            max_bytes=Settings.WEB_MAX_RESPONSE_BYTES,
        )
    return _fetcher


def _get_extractor() -> ContentExtractor:
    global _extractor
    if _extractor is None:
        _extractor = ContentExtractor()
    return _extractor


@tool(
    description=(
        "Search the web for current external information. "
        "Use when the user's question requires knowledge about external libraries, "
        "documentation, recent events, version numbers, or anything not in the "
        "local repository. Do NOT use for questions answerable from the codebase. "
        "Returns a list of results with URL, title, and snippet."
    ),
    parameters={
        "query": "str",
        "max_results": "int",
    },
    returns="list[dict]",
)
def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web and return a list of result dicts.

    Each dict contains: url, title, snippet, source (domain).
    Returns an empty list with a warning if the search provider fails.
    """
    if not query.strip():
        raise ValueError("'query' must not be empty.")

    max_results = max(1, min(max_results, 20))

    try:
        searcher = create_searcher(Settings.WEB_SEARCH_PROVIDER)
        results = searcher.search(query, max_results=max_results)
        logger.info("web_search: %d results for %r", len(results), query)
        return [
            {
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet,
                "source": r.source,
            }
            for r in results
        ]
    except WebSearchError as exc:
        logger.warning("web_search failed: %s", exc)
        return [{"error": str(exc), "url": "", "title": "", "snippet": "", "source": ""}]


@tool(
    description=(
        "Fetch and extract the text content of a web page. "
        "Use after web_search when you need the full text of a specific URL. "
        "Returns extracted title and text. Rejects private IPs, "
        "unsupported schemes, and oversized pages."
    ),
    parameters={"url": "str"},
    returns="dict",
)
def web_fetch(url: str) -> dict:
    """
    Fetch a single URL and return extracted content.

    Returns a dict: {url, title, text, content_type, chars}.
    On failure returns {url, error}.
    """
    if not url.strip():
        raise ValueError("'url' must not be empty.")

    try:
        raw = _get_fetcher().fetch(url)
        doc = _get_extractor().extract(raw)
        return {
            "url": doc.url,
            "title": doc.title,
            "text": doc.text,
            "content_type": raw.content_type,
            "chars": len(doc.text),
        }
    except FetchError as exc:
        logger.info("web_fetch failed for %s: %s", url, exc)
        return {"url": url, "error": str(exc)}
    except Exception as exc:
        logger.warning("Unexpected error in web_fetch for %s: %s", url, exc)
        return {"url": url, "error": f"Unexpected error: {exc}"}


@tool(
    description=(
        "Perform a full web research pipeline: search → fetch → extract → rank → "
        "return bounded, source-attributed evidence ready to use in an LLM response. "
        "Use this for questions about external libraries, versions, documentation, "
        "current events, or any knowledge not in the local repository. "
        "Returns a formatted string with sources and citations. "
        "Do NOT use for pure repository questions."
    ),
    parameters={
        "query": "str",
    },
    returns="str",
)
def web_context(query: str) -> str:
    """
    Build a full WebContext for query and return it as a formatted string.

    The string is ready to be included directly in an LLM prompt.
    It contains source titles, URLs, and relevant excerpts with a total
    character budget enforced by Settings.WEB_EVIDENCE_CHARS * WEB_MAX_RESULTS.

    On complete failure returns a message explaining that web research
    is unavailable — never crashes, never fabricates citations.
    """
    if not query.strip():
        raise ValueError("'query' must not be empty.")

    ctx: WebContext = _get_service().build_context(query)
    return ctx.format_for_llm()
