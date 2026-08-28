"""
Provider backend for Anthropic's Claude models.

The `anthropic` package is an optional dependency: it is only
imported when this provider is actually instantiated, so Pearl does
not require it unless Claude is the configured provider.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from src.llm.providers.base import LLMProvider


class ClaudeProvider(LLMProvider):
    """
    Provider backend for Anthropic's Messages API.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required to use the Claude "
                "provider. Install it with `pip install anthropic`."
            ) from exc

        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model

        self.TRANSIENT_ERRORS = (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )

        if not response.content:
            raise ValueError("No content returned from model.")

        text = getattr(response.content[0], "text", None)

        if not text:
            raise ValueError("Model returned an empty response.")

        return text.strip()

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Iterator[str]:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        ) as stream:
            for text_chunk in stream.text_stream:
                yield text_chunk
