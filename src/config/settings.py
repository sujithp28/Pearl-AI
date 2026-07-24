"""
Global configuration for Pearl.
"""

from pathlib import Path
import os

import torch
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ==================================================
    # Project
    # ==================================================

    PROJECT_NAME = "Pearl"
    VERSION = "0.1.0"

    # ==================================================
    # Directories
    # ==================================================

    ROOT_DIR = Path(__file__).resolve().parents[2]

    SRC_DIR = ROOT_DIR / "src"

    MODELS_DIR = ROOT_DIR / "models"

    LOGS_DIR = ROOT_DIR / "logs"

    PROMPTS_DIR = SRC_DIR / "prompts"

    # ==================================================
    # Device
    # ==================================================

    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

    # ==================================================
    # LLM Provider Selection
    # ==================================================

    # Which provider LLMClient() connects to by default.
    # One of: ollama, openai, openrouter, custom, claude, anthropic, gemini.
    LLM_PROVIDER = os.getenv("PEARL_LLM_PROVIDER", "ollama")

    # -- OpenAI --

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

    OPENAI_BASE_URL = os.getenv(
        "OPENAI_BASE_URL", "https://api.openai.com/v1"
    )

    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # -- Anthropic (Claude) --

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    # -- Google (Gemini) --

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # -- OpenRouter --

    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

    OPENROUTER_BASE_URL = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )

    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/auto")

    # -- Ollama --

    OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")

    OLLAMA_BASE_URL = os.getenv(
        "OLLAMA_BASE_URL", "http://localhost:11434/v1"
    )

    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

    # -- Any other OpenAI-compatible endpoint --

    CUSTOM_API_KEY = os.getenv("CUSTOM_API_KEY", "")

    CUSTOM_BASE_URL = os.getenv("CUSTOM_BASE_URL", "")

    CUSTOM_MODEL = os.getenv("CUSTOM_MODEL", "")

    # ==================================================
    # Local Model (Keep this for now)
    # ==================================================

    MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"

    MODEL_CACHE_DIR = MODELS_DIR

    # ==================================================
    # Generation
    # ==================================================

    MAX_NEW_TOKENS = 1024

    TEMPERATURE = 0.2

    TOP_P = 0.95

    # ==================================================
    # Prompt files
    # ==================================================

    TOOL_SELECTION_PROMPT = (
        PROMPTS_DIR / "tool_selection.txt"
    )

    # ==================================================
    # Banner
    # ==================================================

    BANNER = f"""
==================================================
{PROJECT_NAME} v{VERSION}
==================================================
"""


settings = Settings()