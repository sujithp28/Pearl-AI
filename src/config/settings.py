"""
Global configuration for Pearl.
"""

from pathlib import Path

import torch


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
    # Model
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