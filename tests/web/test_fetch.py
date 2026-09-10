"""Tests for src/web/fetch.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.web.fetch import (
    FetchError,
    PageFetcher,
    PrivateAddressError,
    ResponseTooLargeError,
    UnsupportedSchemeError,
    _resolve_and_check_ip,
    _validate_scheme,
)


class TestValidateScheme:
    def test_http_is_allowed(self) -> None:
        _validate_scheme("http://example.com/page")  # no exception

    def test_https_is_allowed(self) -> None:
        _validate_scheme("https://example.com/page")  # no exception

    def test_file_scheme_rejected(self) -> None:
        with pytest.raises(UnsupportedSchemeError, match="file"):
            _validate_scheme("file:///etc/passwd")

    def test_ftp_scheme_rejected(self) -> None:
        with pytest.raises(UnsupportedSchemeError, match="ftp"):
            _validate_scheme("ftp://files.example.com/thing")

    def test_data_scheme_rejected(self) -> None:
        with pytest.raises(UnsupportedSchemeError):
            _validate_scheme("data:text/html,<h1>hello</h1>")

    def test_empty_scheme_rejected(self) -> None:
        with pytest.raises(UnsupportedSchemeError):
            _validate_scheme("//no-scheme.example.com")


class TestResolveAndCheckIp:
    @patch("src.web.fetch.socket.getaddrinfo")
    def test_public_ip_allowed(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("8.8.8.8", 0))]
        _resolve_and_check_ip("dns.google")  # no exception

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_localhost_127_blocked(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
        with pytest.raises(PrivateAddressError):
            _resolve_and_check_ip("localhost")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_rfc1918_10_blocked(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("10.0.0.1", 0))]
        with pytest.raises(PrivateAddressError):
            _resolve_and_check_ip("internal.example.com")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_rfc1918_192168_blocked(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("192.168.1.1", 0))]
        with pytest.raises(PrivateAddressError):
            _resolve_and_check_ip("router.local")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_link_local_blocked(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("169.254.0.1", 0))]
        with pytest.raises(PrivateAddressError):
            _resolve_and_check_ip("link.local.host")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_dns_failure_raises_fetch_error(self, mock_gai) -> None:
        import socket as _socket

        mock_gai.side_effect = _socket.gaierror("Name or service not known")
        with pytest.raises(FetchError, match="Cannot resolve"):
            _resolve_and_check_ip("no-such-host.invalid")


class TestPageFetcher:
    def _make_response(
        self,
        body: bytes = b"<html><body>hello</body></html>",
        content_type: str = "text/html",
        url: str = "https://example.com/page",
        headers: dict | None = None,
    ):
        resp = MagicMock()
        resp.content = body
        resp.headers = MagicMock()
        resp.headers.get = lambda k, d="": (headers or {}).get(k, d)
        resp.url = MagicMock()
        resp.url.__str__ = lambda s: url
        resp.raise_for_status = MagicMock()
        return resp

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_fetch_success(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        fetcher = PageFetcher(timeout=5.0, max_bytes=1024 * 1024)
        resp = self._make_response()

        with patch("src.web.fetch.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = resp
            mock_client_cls.return_value = mock_client

            raw = fetcher.fetch("https://example.com/page")

        assert raw.url == "https://example.com/page"
        assert raw.body == b"<html><body>hello</body></html>"
        assert "html" in raw.content_type

    def test_rejects_file_scheme(self) -> None:
        fetcher = PageFetcher()
        with pytest.raises(UnsupportedSchemeError):
            fetcher.fetch("file:///etc/passwd")

    def test_rejects_ftp_scheme(self) -> None:
        fetcher = PageFetcher()
        with pytest.raises(UnsupportedSchemeError):
            fetcher.fetch("ftp://ftp.example.com/file")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_rejects_private_ip(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("192.168.0.1", 0))]
        fetcher = PageFetcher()
        with pytest.raises(PrivateAddressError):
            fetcher.fetch("http://internal.example.com/")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_rejects_oversized_response(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        fetcher = PageFetcher(max_bytes=100)
        resp = self._make_response(body=b"x" * 200)

        with patch("src.web.fetch.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = resp
            mock_cls.return_value = mock_client

            with pytest.raises(ResponseTooLargeError):
                fetcher.fetch("https://example.com/")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_timeout_raises_fetch_error(self, mock_gai) -> None:
        import httpx as _httpx

        mock_gai.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        fetcher = PageFetcher()

        with patch("src.web.fetch.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = _httpx.TimeoutException("timed out")
            mock_cls.return_value = mock_client

            with pytest.raises(FetchError, match="timed out"):
                fetcher.fetch("https://example.com/")

    @patch("src.web.fetch.socket.getaddrinfo")
    def test_content_length_header_triggers_size_check(self, mock_gai) -> None:
        mock_gai.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        fetcher = PageFetcher(max_bytes=100)
        resp = self._make_response(body=b"small", headers={"content-length": "999999"})

        with patch("src.web.fetch.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = resp
            mock_cls.return_value = mock_client

            with pytest.raises(ResponseTooLargeError):
                fetcher.fetch("https://example.com/")
