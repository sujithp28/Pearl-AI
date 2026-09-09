"""
Safe HTTP page fetcher.

Security requirements enforced here:
- Private / loopback IP blocking (RFC 1918, RFC 3927, loopback, link-local)
- Scheme allowlist: http and https only
- Maximum response size
- Timeout
- Content-type validation gate (callers decide whether to extract)
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse

import httpx

from src.web.models import RawPage

logger = logging.getLogger(__name__)

# Schemes accepted for fetch. file://, ftp://, data: etc. are rejected.
_ALLOWED_SCHEMES = {"http", "https"}

# Networks that must never be fetched — they're internal infrastructure.
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_ALLOWED_CONTENT_TYPES = {
    "text/html",
    "text/plain",
    "application/xhtml+xml",
}


class FetchError(Exception):
    """Raised for all page-fetch failures."""


class PrivateAddressError(FetchError):
    """Raised when the resolved IP is a private/internal address."""


class UnsupportedSchemeError(FetchError):
    """Raised when the URL scheme is not http/https."""


class UnsupportedContentTypeError(FetchError):
    """Raised when the response content-type is not extractable."""


class ResponseTooLargeError(FetchError):
    """Raised when the response body exceeds the configured limit."""


def _validate_scheme(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsupportedSchemeError(
            f"Scheme {scheme!r} is not allowed. Only http/https are supported."
        )


def _resolve_and_check_ip(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise FetchError(f"Cannot resolve hostname {hostname!r}: {exc}") from exc

    for info in infos:
        addr_str = info[4][0]
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            continue

        for blocked in _BLOCKED_NETWORKS:
            if addr in blocked:
                raise PrivateAddressError(
                    f"URL resolves to a private/internal address ({addr}) — blocked."
                )


class PageFetcher:
    """
    Fetches a single URL and returns the raw response body.

    Does not extract or parse content — that is ContentExtractor's job.
    """

    def __init__(self, timeout: float = 15.0, max_bytes: int = 5 * 1024 * 1024) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes

    def fetch(self, url: str) -> RawPage:
        """
        Fetch url and return a RawPage.

        Raises FetchError (or a subclass) for all failures:
        unsupported scheme, private IP, timeout, too large, HTTP error, etc.
        Never propagates raw httpx or socket exceptions to callers.
        """
        _validate_scheme(url)

        parsed = urllib.parse.urlparse(url)
        _resolve_and_check_ip(parsed.hostname or "")

        logger.debug("Fetching: %s", url)

        try:
            with httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                max_redirects=5,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; Pearl-AI/1.2; "
                        "+https://github.com/sujithp28/Pearl-AI)"
                    )
                },
            ) as client:
                resp = client.get(url)

                # Re-validate the final URL after redirects.
                final_url = str(resp.url)
                final_parsed = urllib.parse.urlparse(final_url)
                _validate_scheme(final_url)
                _resolve_and_check_ip(final_parsed.hostname or "")

                # Content-length check before reading body.
                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        cl = int(content_length)
                        if cl > self._max_bytes:
                            raise ResponseTooLargeError(
                                f"Content-Length {cl} exceeds limit {self._max_bytes}."
                            )
                    except ValueError:
                        pass

                body = resp.content
                if len(body) > self._max_bytes:
                    raise ResponseTooLargeError(
                        f"Response body ({len(body)} bytes) exceeds limit {self._max_bytes}."
                    )

                resp.raise_for_status()

                ct_raw = resp.headers.get("content-type", "text/html")
                content_type = ct_raw.split(";")[0].strip().lower()

                logger.debug("Fetched %s — %d bytes, %s", final_url, len(body), content_type)

                return RawPage(
                    url=final_url,
                    content_type=content_type,
                    body=body,
                    original_url=url,
                )

        except (FetchError, ResponseTooLargeError, PrivateAddressError, UnsupportedSchemeError):
            raise
        except httpx.TimeoutException as exc:
            raise FetchError(f"Request timed out for {url}: {exc}") from exc
        except httpx.TooManyRedirects as exc:
            raise FetchError(f"Too many redirects for {url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise FetchError(
                f"HTTP {exc.response.status_code} for {url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"Network error for {url}: {exc}") from exc
