"""
Run Pearl's MCP server over stdio.

Usage:
    python -m src.mcp
"""

from __future__ import annotations

import logging

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.main import build_registry
from src.mcp.server import MCPServer
from src.memory import Memory


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
    dispatcher = ToolDispatcher(registry)
    memory = Memory()
    planner = Planner(registry, dispatcher)

    server = MCPServer(
        registry,
        dispatcher=dispatcher,
        planner=planner,
        memory=memory,
    )

    server.run_stdio()


if __name__ == "__main__":
    main()
