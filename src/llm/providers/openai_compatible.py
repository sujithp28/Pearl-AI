"""
Provider backend for any OpenAI-compatible chat completions API:
OpenAI itself, OpenRouter, Ollama's `/v1` endpoint, and any other
OpenAI-compatible endpoint all speak this same wire protocol, so they
share one implementation.
"""

from __future__ import annotations

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
    ) -> None:
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self.model = model

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if not response.choices:
            raise ValueError("No choices returned from model.")

        content = response.choices[0].message.content

        if content is None:
            raise ValueError("Model returned an empty response.")

        return content.strip()
