"""
Run Pearl's MCP server over stdio.

Usage:
    python -m src.mcp
"""

from __future__ import annotations

import logging
import threading

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.llm.client import LLMClient
from src.main import build_registry
from src.mcp.server import MCPServer
from src.memory import Memory
from src.tools.repo_tools import build_startup_index

logger = logging.getLogger(__name__)


def _warm_up_llm(llm: LLMClient) -> None:
    """
    Send a minimal prompt to the LLM provider so the model is loaded
    into GPU/CPU memory before the user sends their first message.

    Runs in a daemon thread: it never blocks server startup, and it
    dies automatically when the process exits. A failure here (e.g.
    Ollama not running, model not pulled) is logged but never raises —
    the user will just see the normal cold-start latency.
    """
    try:
        logger.info("Warming up LLM model in background...")
        # consume the whole stream so Ollama actually loads the model
        text = "".join(llm.generate_stream("hi"))
        logger.info("LLM warm-up done (%d chars).", len(text))
    except Exception as exc:
        logger.warning("LLM warm-up failed (cold start expected): %s", exc)


def main() -> None:
    """
    Build Pearl's tool registry and serve it over MCP via stdio.
    """

    # Logs must go to stderr, not stdout: stdout is reserved for the
    # JSON-RPC protocol stream. logging.basicConfig() defaults to
    # stderr, so no `stream=` override is needed here.
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    registry = build_registry()
    build_startup_index()
    dispatcher = ToolDispatcher(registry)
    memory = Memory()
    llm = LLMClient()
    planner = Planner(registry, dispatcher, llm)

    # Warm up the model in the background so it is in RAM by the time
    # the user sends their first message, avoiding a cold-start delay.
    warmup = threading.Thread(target=_warm_up_llm, args=(llm,), daemon=True)
    warmup.start()

    server = MCPServer(
        registry,
        dispatcher=dispatcher,
        planner=planner,
        memory=memory,
        llm=llm,
    )

    server.run_stdio()


if __name__ == "__main__":
    main()
