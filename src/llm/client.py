"""
LLM Client for Pearl using OmniRoute.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Transient errors worth retrying with backoff.
TRANSIENT_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class LLMClient:
    """
    Wrapper around OmniRoute (OpenAI-compatible API).
    """

    def __init__(self) -> None:
        logger.info("Connecting to OmniRoute...")

        self.client = OpenAI(
            api_key=Settings.OMNIROUTE_API_KEY,
            base_url=Settings.OMNIROUTE_BASE_URL,
            timeout=60.0,  # Prevent hanging forever
        )

        self.model = Settings.OMNIROUTE_MODEL

        logger.info("Connected to OmniRoute.")
        logger.info("Using model: %s", self.model)

    def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_new_tokens: int | None = None,
    ) -> str:
        """
        Send prompt to OmniRoute and return model response.

        Retries transient API/network failures with exponential backoff.
        """

        if temperature is None:
            temperature = Settings.TEMPERATURE

        if max_new_tokens is None:
            max_new_tokens = Settings.MAX_NEW_TOKENS

        response = None
        attempt = 0

        while response is None:
            logger.info("Sending request to OmniRoute...")

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    temperature=temperature,
                    max_tokens=max_new_tokens,
                )

                logger.info("Received response from OmniRoute.")

            except TRANSIENT_ERRORS as exc:
                attempt += 1

                if attempt > MAX_RETRIES:
                    logger.exception(
                        "OmniRoute request failed after %d attempts.",
                        attempt,
                    )
                    raise

                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))

                logger.warning(
                    "Transient OmniRoute error (attempt %d/%d): %s. "
                    "Retrying in %.1fs...",
                    attempt,
                    MAX_RETRIES,
                    exc,
                    delay,
                )

                time.sleep(delay)

            except Exception:
                logger.exception("OmniRoute request failed.")
                raise

        if not response.choices:
            raise ValueError("No choices returned from model.")

        content = response.choices[0].message.content

        if content is None:
            raise ValueError("Model returned an empty response.")

        return content.strip()

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