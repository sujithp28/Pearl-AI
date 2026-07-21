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

    # ==================================================
    # Device
    # ==================================================
    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # ==================================================
    # Model
    # ==================================================
    DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"

    MODEL_CACHE_DIR = MODELS_DIR

    # ==================================================
    # Generation
    # ==================================================
    MAX_NEW_TOKENS = 1024
    TEMPERATURE = 0.2
    TOP_P = 0.95

    # ==================================================
    # Agent Prompt
    # ==================================================
    SYSTEM_PROMPT = f"""
You are {PROJECT_NAME}.

You are an expert AI coding assistant.

Rules:

- Answer only what the user asks.
- Never repeat the user's prompt.
- Never hallucinate.
- Keep responses concise.
- Generate clean production-quality code.
- Think before answering.
"""

    # ==================================================
    # Banner
    # ==================================================
    BANNER = f"""
==================================================
{PROJECT_NAME} v{VERSION}
==================================================
"""


settings = Settings()