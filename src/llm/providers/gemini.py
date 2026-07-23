"""
Provider backend for Google's Gemini models.

The `google-generativeai` package is an optional dependency: it is
only imported when this provider is actually instantiated, so Pearl
does not require it unless Gemini is the configured provider.
"""

from __future__ import annotations

from typing import Any

from src.llm.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    """
    Provider backend for the Gemini API.
    """

    def __init__(self, api_key: str, model: str) -> None:
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "The 'google-generativeai' package is required to use "
                "the Gemini provider. Install it with "
                "`pip install google-generativeai`."
            ) from exc

        genai.configure(api_key=api_key)

        self._model = genai.GenerativeModel(model)

        try:
            from google.api_core import exceptions as google_exceptions

            self.TRANSIENT_ERRORS = (
                google_exceptions.DeadlineExceeded,
                google_exceptions.ServiceUnavailable,
                google_exceptions.ResourceExhausted,
                google_exceptions.InternalServerError,
            )
        except ImportError:
            self.TRANSIENT_ERRORS = ()

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        prompt = "\n\n".join(message["content"] for message in messages)

        response = self._model.generate_content(
            prompt,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
        )

        text = getattr(response, "text", None)

        if not text:
            raise ValueError("Model returned an empty response.")

        return text.strip()
