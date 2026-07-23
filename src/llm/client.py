"""
LLM Client for Pearl.

Provider-agnostic: the active backend (OmniRoute, OpenAI, Claude,
Gemini, OpenRouter, or Ollama) is selected via `src.llm.providers`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from src.config.settings import Settings
from src.llm.providers.base import LLMProvider
from src.llm.providers.factory import create_provider

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class LLMClient:
    """
    Provider-agnostic LLM client.

    Delegates the actual completion call to an `LLMProvider` backend
    (OmniRoute by default, matching Pearl's original behavior), so
    the tool selector, planner, and `PearlAgent.chat` do not need to
    know which provider is active.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        provider_name: str | None = None,
    ) -> None:
        name = provider_name or Settings.LLM_PROVIDER

        logger.info("Connecting to LLM provider: %s", name)

        self.provider = provider or create_provider(name)

        logger.info(
            "Connected to provider: %s",
            type(self.provider).__name__,
        )

    def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_new_tokens: int | None = None,
    ) -> str:
        """
        Send prompt to the active provider and return its response.

        Retries transient API/network failures with exponential backoff.
        """

        if temperature is None:
            temperature = Settings.TEMPERATURE

        if max_new_tokens is None:
            max_new_tokens = Settings.MAX_NEW_TOKENS

        messages = [
            {
                "role": "user",
                "content": prompt,
            }
        ]

        text = None
        attempt = 0

        while text is None:
            logger.info("Sending request to provider...")

            try:
                text = self.provider.complete(
                    messages,
                    temperature,
                    max_new_tokens,
                )

                logger.info("Received response from provider.")

            except self.provider.TRANSIENT_ERRORS as exc:
                attempt += 1

                if attempt > MAX_RETRIES:
                    logger.exception(
                        "Provider request failed after %d attempts.",
                        attempt,
                    )
                    raise

                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))

                logger.warning(
                    "Transient provider error (attempt %d/%d): %s. "
                    "Retrying in %.1fs...",
                    attempt,
                    MAX_RETRIES,
                    exc,
                    delay,
                )

                time.sleep(delay)

            except Exception:
                logger.exception("Provider request failed.")
                raise

        return text

    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from model output.

        Supports:
        - Plain JSON
        - Markdown JSON
        - Explanatory text containing JSON
        """

        text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
        text = text.replace("```", "").strip()

        try:
            json.loads(text)
            return text
        except Exception:
            pass

        candidate = self._find_balanced_json_object(text)

        if candidate is not None:
            return candidate

        raise ValueError(
            f"No JSON object found.\n\nModel returned:\n{text}"
        )

    @staticmethod
    def _find_balanced_json_object(text: str) -> str | None:
        """
        Find the first balanced `{...}` object in `text`.

        Scans brace depth (ignoring braces inside string literals)
        instead of using a greedy regex, so trailing prose containing
        braces cannot be captured into the match.
        """

        start = text.find("{")

        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1

                if depth == 0:
                    return text[start : index + 1]

        return None

    def generate_json(
        self,
        prompt: str,
    ) -> dict[str, Any]:
        """
        Generate JSON from the LLM.
        """

        response = self.generate(prompt)

        logger.debug("Raw model response: %s", response)

        cleaned = self._extract_json(response)

        logger.debug("Cleaned JSON: %s", cleaned)

        try:
            payload = json.loads(cleaned)

            logger.debug("Parsed payload: %s", payload)

            return payload

        except json.JSONDecodeError as exc:
            logger.debug("Invalid JSON from model: %s", cleaned)

            raise ValueError(
                "Model returned invalid JSON."
            ) from exc

    @staticmethod
    def save_response(
        response: str,
        path: str,
    ) -> None:
        """
        Save model response to a file.
        """

        Path(path).write_text(
            response,
            encoding="utf-8",
        )

    @staticmethod
    def load_prompt(
        path: str,
    ) -> str:
        """
        Load prompt template from disk.
        """

        return Path(path).read_text(
            encoding="utf-8",
        )