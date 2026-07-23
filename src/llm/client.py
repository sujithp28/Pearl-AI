"""
LLM Client for Pearl using OmniRoute.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.config.settings import Settings

logger = logging.getLogger(__name__)


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
        """

        if temperature is None:
            temperature = Settings.TEMPERATURE

        if max_new_tokens is None:
            max_new_tokens = Settings.MAX_NEW_TOKENS

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

        match = re.search(r"\{.*\}", text, re.DOTALL)

        if match:
            return match.group(0)

        raise ValueError(
            f"No JSON object found.\n\nModel returned:\n{text}"
        )

    def generate_json(
        self,
        prompt: str,
    ) -> dict[str, Any]:
        """
        Generate JSON from the LLM.
        """

        response = self.generate(prompt)

        print("\n========== RAW MODEL RESPONSE ==========")
        print(response)
        print("========================================\n")

        cleaned = self._extract_json(response)

        print("\n========== CLEANED JSON ==========")
        print(cleaned)
        print("==================================\n")

        try:
            payload = json.loads(cleaned)

            print("\n========== PARSED PAYLOAD ==========")
            print(payload)
            print(type(payload))
            print("====================================\n")

            return payload

        except json.JSONDecodeError as exc:
            print("\n========== JSON ERROR ==========")
            print(cleaned)
            print("================================\n")

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