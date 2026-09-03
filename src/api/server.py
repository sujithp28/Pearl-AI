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
    event_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _chat_worker() -> None:
        try:
            for event_type, data in session.chat_stream(req.message):
                asyncio.run_coroutine_threadsafe(
                    event_queue.put((event_type, data)), loop
                )
        finally:
            asyncio.run_coroutine_threadsafe(event_queue.put(None), loop)

    threading.Thread(target=_chat_worker, daemon=True, name="pearl-chat").start()

    async def _generate() -> AsyncIterator[str]:
        while True:
            item = await event_queue.get()
            if item is None:
                yield _sse("done", {})
                break
            event_type, data = item
            if event_type == "chunk":
                yield _sse("chunk", {"text": data})
            elif event_type == "error":
                yield _sse("error", {"text": data})
            # "done" from session is ignored — we emit our own sentinel above

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
    loop = asyncio.get_event_loop()

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


# ------------------------------------------------------------------ provider


_PROVIDER_DISPLAY: dict[str, str] = {
    "pearl": "Pearl",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "claude": "Anthropic",
    "openrouter": "OpenRouter",
    "gemini": "Google AI",
    "custom": "Custom endpoint",
    "scripted": "Scripted (testing)",
}


def _provider_display(name: str) -> str:
    return _PROVIDER_DISPLAY.get(name, name.title())


def _local_inference_available() -> bool:
    """True when llama-cpp-python is importable (local model can run)."""
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False


def _provider_configured(name: str) -> bool:
    from src.config.settings import Settings
    if name == "pearl":
        # Pearl is always "configured": either via explicit key (remote)
        # or via local inference (auto-downloaded model, no key needed).
        return bool(Settings.PEARL_INFERENCE_API_KEY) or _local_inference_available()
    if name == "openai":
        return bool(Settings.OPENAI_API_KEY)
    if name in ("anthropic", "claude"):
        return bool(Settings.ANTHROPIC_API_KEY)
    if name == "openrouter":
        return bool(Settings.OPENROUTER_API_KEY)
    if name == "gemini":
        return bool(Settings.GEMINI_API_KEY)
    if name == "custom":
        return bool(Settings.CUSTOM_API_KEY and Settings.CUSTOM_BASE_URL)
    return False


def _provider_model(name: str) -> str | None:
    from src.config.settings import Settings
    if name == "pearl":
        if Settings.PEARL_INFERENCE_API_KEY:
            return Settings.PEARL_INFERENCE_CHAT_MODEL
        return Settings.LOCAL_MODEL_FILE  # local model filename shown as the model
    return {
        "openai": Settings.OPENAI_MODEL,
        "anthropic": Settings.ANTHROPIC_MODEL,
        "claude": Settings.ANTHROPIC_MODEL,
        "openrouter": Settings.OPENROUTER_MODEL,
        "gemini": Settings.GEMINI_MODEL,
        "custom": Settings.CUSTOM_MODEL,
    }.get(name)


@app.get("/api/provider")
async def provider_info() -> JSONResponse:
    from src.config.settings import Settings
    name = Settings.LLM_PROVIDER.lower()
    payload = {
        "provider": name,
        "display": _provider_display(name),
        "configured": _provider_configured(name),
        "model": _provider_model(name),
    }
    # Per-role routing, so the UI can show what actually runs each task
    # rather than one global provider name. Names only — never keys.
    try:
        from src.llm.router import ModelRouter
        roles = ModelRouter().describe()
        payload["profile"] = roles.pop("profile", "")
        payload["roles"] = roles
    except Exception as exc:
        logger.warning("Could not describe model routing: %s", exc)
    return JSONResponse(payload)


class ProviderRequest(BaseModel):
    provider: str


@app.post("/api/provider")
async def set_provider(req: ProviderRequest) -> JSONResponse:
    from src.config.settings import Settings, write_env_key
    from src.llm.providers.factory import SUPPORTED_PROVIDERS

    name = req.provider.strip().lower()
    if name not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {name!r}")

    Settings.LLM_PROVIDER = name
    write_env_key("PEARL_LLM_PROVIDER", name)

    global _session
    if _session is not None:
        _session = PearlSession(_session.workspace)

    return JSONResponse({
        "ok": True,
        "provider": name,
        "display": _provider_display(name),
    })


# ------------------------------------------------------------------ tools


@app.get("/api/tools")
async def tools() -> JSONResponse:
    session = get_session()
    tool_list = [
        {"name": t["name"], "description": t.get("description", "")}
        for t in session.registry.get_tools()
    ]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})


# ------------------------------------------------------------------ sessions (V2)

from src.agent.session_manager import get_session_manager


class RenameSessionRequest(BaseModel):
    title: str


@app.get("/api/sessions")
async def list_sessions() -> JSONResponse:
    """List all persistent sessions, newest first."""
    manager = get_session_manager()
    return JSONResponse({"sessions": manager.list_sessions()})


@app.post("/api/sessions")
async def create_session_endpoint() -> JSONResponse:
    """Create a new persistent session for the current workspace."""
    session = get_session()
    manager = get_session_manager()
    sid = manager.create_session(workspace=str(session.workspace))
    return JSONResponse({"session_id": sid})


@app.get("/api/sessions/{session_id}")
async def get_session_endpoint(session_id: str) -> JSONResponse:
    manager = get_session_manager()
    try:
        rec = manager.get_session(session_id)
        return JSONResponse(rec.to_dict())
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")


@app.patch("/api/sessions/{session_id}")
async def rename_session_endpoint(
    session_id: str, req: RenameSessionRequest
) -> JSONResponse:
    manager = get_session_manager()
    try:
        manager.rename_session(session_id, req.title)
        return JSONResponse({"ok": True})
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str) -> JSONResponse:
    manager = get_session_manager()
    manager.delete_session(session_id)
    return JSONResponse({"ok": True})


@app.get("/api/sessions/{session_id}/history")
async def session_history_endpoint(session_id: str) -> JSONResponse:
    manager = get_session_manager()
    try:
        history = manager.load_history(session_id)
        return JSONResponse({"messages": history})
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")


# ------------------------------------------------------------------ helpers


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
