"""
HTML → clean text content extractor.

Turns a RawPage into a WebDocument by:
1. Stripping non-content elements (nav, scripts, styles, ads, etc.)
2. Extracting the title
3. Collecting headings for structure
4. Extracting clean body text

Falls back to raw-text stripping when BeautifulSoup parsing fails so
that malformed HTML never crashes the pipeline.
"""

from __future__ import annotations

import logging
import re

from src.web.models import RawPage, WebDocument

logger = logging.getLogger(__name__)

# Tags whose entire subtree is stripped during extraction.
_STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "iframe",
    "figure",
    "figcaption",
    "video",
    "audio",
    "canvas",
    "advertisement",
    "aside",
}

# Heading tags used to populate WebDocument.headings.
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}


def _decode_body(body: bytes) -> str:
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    return body.decode("utf-8", errors="replace")


def _extract_with_bs4(raw: RawPage) -> WebDocument:
    from bs4 import BeautifulSoup, Tag

    html = _decode_body(raw.body)
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)

    # Remove non-content subtrees.
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    # Collect headings before stripping structure.
    headings: list[str] = []
    for h_tag in soup.find_all(_HEADING_TAGS):
        text = h_tag.get_text(strip=True)
        if text:
            headings.append(text)

    # Extract body text from the content area.
    # Try <article>, <main>, then <body>, then the whole document.
    content_root = (
        soup.find("article") or soup.find("main") or soup.find("body") or soup
    )

    raw_text = content_root.get_text(separator=" ", strip=True) if content_root else ""
    text = _clean_whitespace(raw_text)

    # Fall back to using the page title from og:title or h1 if <title> was missing.
    if not title and headings:
        title = headings[0]
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title and isinstance(og_title, Tag):
            title = og_title.get("content", "") or ""

    return WebDocument(url=raw.url, title=title, text=text, headings=headings)


def _extract_plain_text(raw: RawPage) -> WebDocument:
    """Fallback for plain text responses or when BS4 parsing fails."""
    text = _decode_body(raw.body)
    text = _clean_whitespace(text)
    return WebDocument(url=raw.url, title=raw.url, text=text, headings=[])


def _clean_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ContentExtractor:
    """
    Convert a RawPage into a WebDocument.

    Always returns a WebDocument — even if the page is malformed or the
    content-type is unexpected. Never raises from user-visible callers.
    """

    def extract(self, raw: RawPage) -> WebDocument:
        ct = raw.content_type.lower()

        if "html" in ct or "xhtml" in ct:
            try:
                return _extract_with_bs4(raw)
            except Exception as exc:
                logger.warning(
                    "BS4 extraction failed for %s (%s); falling back to text strip.",
                    raw.url,
                    exc,
                )
                return _extract_plain_text(raw)

        # text/plain or anything else — strip directly.
        return _extract_plain_text(raw)
