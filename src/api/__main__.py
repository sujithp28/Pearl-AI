"""
Pearl standalone server entry point.

Usage:
    python -m src.api                          # workspace = current directory
    python -m src.api --workspace /path/to/project
    python -m src.api --port 7474
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pearl AI standalone server")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Project directory to open (default: current directory)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7474,
        help="Port to listen on (default: 7474)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open the browser automatically",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"Error: workspace directory not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    # Initialise the session before starting uvicorn
    from src.api.server import init_session, app
    init_session(workspace)

    import uvicorn

    url = f"http://localhost:{args.port}"
    logger.info("Pearl AI starting at %s", url)
    logger.info("Workspace: %s", workspace)

    if not args.no_browser:
        import threading
        import time
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
