"""
LLM Client for Pearl.

Provides a simple interface around Hugging Face models.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM
from transformers import AutoTokenizer

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Wrapper around the Hugging Face model.
    """

    def __init__(self) -> None:
        logger.info("Loading model...")

        self.device = Settings.DEVICE

        self.tokenizer = AutoTokenizer.from_pretrained(
            Settings.MODEL_NAME
        )

        self.model = AutoModelForCausalLM.from_pretrained(
            Settings.MODEL_NAME,
            torch_dtype=torch.float16
            if self.device == "cuda"
            else torch.float32,
        )

        self.model.to(self.device)

        logger.info("Model loaded.")

    def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_new_tokens: int | None = None,
    ) -> str:
        """
        Generate text from the model.
        """

        if temperature is None:
            temperature = Settings.TEMPERATURE

        if max_new_tokens is None:
            max_new_tokens = Settings.MAX_NEW_TOKENS

        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            output = self.model.generate(
                **inputs,
                temperature=temperature,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        text = self.tokenizer.decode(
            output[0],
            skip_special_tokens=True,
        )

        return text[len(prompt):].strip()

    def generate_json(
        self,
        prompt: str,
    ) -> dict[str, Any]:
        """
        Generate JSON output from the model.
        """

        response = self.generate(prompt)

        try:
            return json.loads(response)

        except json.JSONDecodeError as exc:
            logger.error(response)
            raise ValueError(
                "Model returned invalid JSON."
            ) from exc

    @staticmethod
    def save_response(
        response: str,
        path: str,
    ) -> None:
        """
        Save an LLM response.
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
        Load a prompt file.
        """

        return Path(path).read_text(
            encoding="utf-8",
        )