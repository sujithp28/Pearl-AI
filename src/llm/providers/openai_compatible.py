"""
Provider backend for any OpenAI-compatible chat completions API:
OpenAI itself, OpenRouter, local endpoints (LM Studio, llama.cpp, etc.),
and any other OpenAI-compatible endpoint all speak this same wire protocol,
so they share one implementation.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from src.llm.providers.base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    """
    Provider backend for any OpenAI-compatible chat completions API.
    """

    TRANSIENT_ERRORS = (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        InternalServerError,
    )

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        keep_alive: str | None = None,
    ) -> None:
        # Defer OpenAI client construction to first use: openai SDK v2+
        # raises at construction time when api_key is absent/empty, which
        # would prevent the MCP server from starting even before the user
        # makes any LLM call.
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._openai_client: OpenAI | None = None
        self.model = model
        self._extra_body = {"keep_alive": keep_alive} if keep_alive else None

    @property
    def client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._openai_client

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        kwargs: dict[str, Any] = {}
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        if not response.choices:
            raise ValueError("No choices returned from model.")

        content = response.choices[0].message.content

        if content is None:
            raise ValueError("Model returned an empty response.")

        return content.strip()

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Iterator[str]:
        kwargs: dict[str, Any] = {}
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
