"""
Pearl standalone HTTP server.

Exposes Pearl Core over HTTP + Server-Sent Events so any browser-based
or desktop client can drive the same agent logic that MCPServer exposes
over stdio.

Routes
------
GET  /                    → serve the standalone web UI (pearl_ui/index.html)
GET  /api/status          → workspace info + connection state
POST /api/chat            → streaming SSE chat response
POST /api/run             → streaming SSE autonomous run
POST /api/approve         → approve pending patches
POST /api/reject          → reject pending patches
POST /api/cancel          → cancel active run
GET  /api/history         → conversation history
GET  /api/workspace       → current workspace info
POST /api/workspace       → change workspace path
GET  /api/tools           → list registered tools
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.api.session import PearlSession, register_loop

logger = logging.getLogger(__name__)

app = FastAPI(title="Pearl AI", version="1.2.0-beta")

# Single global session — Pearl is a local single-user application.
_session: PearlSession | None = None
_UI_DIR = Path(__file__).resolve().parent.parent.parent / "pearl_ui"


def get_session() -> PearlSession:
    if _session is None:
        raise HTTPException(status_code=503, detail="Pearl session not initialized.")
    return _session


def init_session(workspace: Path) -> None:
    global _session
    _session = PearlSession(workspace)


# ------------------------------------------------------------------ startup


@app.on_event("startup")
async def _startup() -> None:
    register_loop(asyncio.get_event_loop())
    logger.info("Pearl API server started.")


# ------------------------------------------------------------------ UI


@app.get("/", response_class=HTMLResponse)
async def serve_ui() -> HTMLResponse:
    ui_file = _UI_DIR / "index.html"
    if not ui_file.exists():
        return HTMLResponse("<h1>Pearl UI not found</h1>", status_code=404)
    return HTMLResponse(ui_file.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ status


@app.get("/api/status")
async def status() -> JSONResponse:
    session = get_session()
    return JSONResponse({
        "ok": True,
        "workspace": session.workspace_info(),
        "running": session.is_running(),
        "awaiting_approval": session.is_awaiting_approval(),
    })


# ------------------------------------------------------------------ chat


class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    session = get_session()

    async def _generate() -> AsyncIterator[str]:
        loop = asyncio.get_event_loop()
        # Run the synchronous generator in a thread pool
        for event_type, data in await loop.run_in_executor(
            None,
            lambda: list(session.chat_stream(req.message)),
        ):
            yield _sse("chunk" if event_type == "chunk" else event_type, {"text": data})
        yield _sse("done", {})

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ------------------------------------------------------------------ run


class RunRequest(BaseModel):
    prompt: str


@app.post("/api/run")
async def run_autonomous(req: RunRequest) -> StreamingResponse:
    session = get_session()

    if session.is_running():
        raise HTTPException(status_code=409, detail="A run is already in progress.")

    event_queue: asyncio.Queue = asyncio.Queue()

    # Run the agent in a background thread
    def _worker() -> None:
        session.run_autonomous_stream(req.prompt, event_queue)
        # Sentinel so SSE knows we're done
        asyncio.run_coroutine_threadsafe(
            event_queue.put(None), asyncio.get_event_loop()
        )

    loop = asyncio.get_event_loop()

    def _start() -> None:
        asyncio.run_coroutine_threadsafe(
            event_queue.put(None), loop  # will be replaced below
        )

    # Actually start the thread properly
    threading.Thread(
        target=lambda: _run_worker(session, req.prompt, event_queue, loop),
        daemon=True,
        name="pearl-run",
    ).start()

    async def _generate() -> AsyncIterator[str]:
        while True:
            event = await event_queue.get()
            if event is None:
                yield _sse("done", {})
                break
            yield _sse(event["type"], event)

    return StreamingResponse(_generate(), media_type="text/event-stream")


def _run_worker(
    session: PearlSession,
    prompt: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    session.run_autonomous_stream(prompt, queue)
    asyncio.run_coroutine_threadsafe(queue.put(None), loop)


# ------------------------------------------------------------------ approval


@app.post("/api/approve")
async def approve() -> JSONResponse:
    session = get_session()
    result = session.approve()
    return JSONResponse(result)


@app.post("/api/reject")
async def reject() -> JSONResponse:
    session = get_session()
    result = session.reject()
    return JSONResponse(result)


@app.post("/api/cancel")
async def cancel() -> JSONResponse:
    session = get_session()
    cancelled = session.cancel()
    return JSONResponse({"cancelled": cancelled})


# ------------------------------------------------------------------ patches


@app.get("/api/patches")
async def patches() -> JSONResponse:
    session = get_session()
    return JSONResponse({
        "files": session.pending_files(),
        "diff": session.pending_diff(),
        "awaiting_approval": session.is_awaiting_approval(),
    })


# ------------------------------------------------------------------ history


@app.get("/api/history")
async def history() -> JSONResponse:
    session = get_session()
    return JSONResponse({"messages": session.conversation_history()})


# ------------------------------------------------------------------ workspace


@app.get("/api/workspace")
async def get_workspace() -> JSONResponse:
    session = get_session()
    return JSONResponse(session.workspace_info())


class WorkspaceRequest(BaseModel):
    path: str


@app.post("/api/workspace")
async def set_workspace(req: WorkspaceRequest) -> JSONResponse:
    global _session
    p = Path(req.path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
    _session = PearlSession(p)
    return JSONResponse({"ok": True, "workspace": _session.workspace_info()})


# ------------------------------------------------------------------ tools


@app.get("/api/tools")
async def tools() -> JSONResponse:
    session = get_session()
    tool_list = [
        {"name": t["name"], "description": t.get("description", "")}
        for t in session.registry.get_tools()
    ]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})


# ------------------------------------------------------------------ helpers


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
