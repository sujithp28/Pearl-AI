"""Tests for src/web/extract.py."""

from __future__ import annotations

from src.web.extract import ContentExtractor
from src.web.models import RawPage


def _make_raw(body: str, content_type: str = "text/html", url: str = "https://x.com") -> RawPage:
    return RawPage(url=url, content_type=content_type, body=body.encode(), original_url=url)


class TestContentExtractor:
    def setup_method(self) -> None:
        self.extractor = ContentExtractor()

    def test_extracts_title(self) -> None:
        raw = _make_raw("<html><head><title>My Title</title></head><body><p>hello</p></body></html>")
        doc = self.extractor.extract(raw)
        assert doc.title == "My Title"

    def test_strips_script_tags(self) -> None:
        raw = _make_raw(
            "<html><body><script>alert('bad')</script><p>good content</p></body></html>"
        )
        doc = self.extractor.extract(raw)
        assert "alert" not in doc.text
        assert "good content" in doc.text

    def test_strips_style_tags(self) -> None:
        raw = _make_raw(
            "<html><body><style>.x{color:red}</style><p>content</p></body></html>"
        )
        doc = self.extractor.extract(raw)
        assert ".x{" not in doc.text
        assert "content" in doc.text

    def test_strips_nav_tags(self) -> None:
        raw = _make_raw(
            "<html><body><nav>Menu Link 1</nav><main><p>article text</p></main></body></html>"
        )
        doc = self.extractor.extract(raw)
        assert "Menu Link 1" not in doc.text
        assert "article text" in doc.text

    def test_extracts_headings(self) -> None:
        raw = _make_raw(
            "<html><body><h1>Main</h1><h2>Sub</h2><p>text</p></body></html>"
        )
        doc = self.extractor.extract(raw)
        assert "Main" in doc.headings
        assert "Sub" in doc.headings

    def test_plain_text_content_type(self) -> None:
        raw = _make_raw("hello world", content_type="text/plain")
        doc = self.extractor.extract(raw)
        assert "hello world" in doc.text

    def test_malformed_html_does_not_raise(self) -> None:
        raw = _make_raw("<html><body><p>unclosed</html>", content_type="text/html")
        doc = self.extractor.extract(raw)
        assert isinstance(doc.text, str)

    def test_empty_body_returns_empty_text(self) -> None:
        raw = _make_raw("")
        doc = self.extractor.extract(raw)
        assert doc.text == "" or len(doc.text.strip()) == 0

    def test_prefers_article_tag_for_content(self) -> None:
        raw = _make_raw(
            "<html><body>"
            "<div id='sidebar'>sidebar noise</div>"
            "<article><p>real article content</p></article>"
            "</body></html>"
        )
        doc = self.extractor.extract(raw)
        assert "real article content" in doc.text

    def test_url_preserved(self) -> None:
        raw = _make_raw("<html><body>text</body></html>", url="https://specific.com/page")
        doc = self.extractor.extract(raw)
        assert doc.url == "https://specific.com/page"

    def test_returns_web_document(self) -> None:
        from src.web.models import WebDocument
        raw = _make_raw("<html><body>text</body></html>")
        doc = self.extractor.extract(raw)
        assert isinstance(doc, WebDocument)
