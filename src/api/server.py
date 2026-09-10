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
import sys
import threading
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.agent.completion import CompletionService
from src.agent.session_manager import get_session_manager
from src.api.session import PearlSession, register_loop
from src.api.tenancy import (
    LOCAL_USER,
    AuthenticationError,
    SessionRegistry,
    auth_required,
    public_mode,
    resolve_user,
)
from src.tools.checkpoint_serialize import (
    checkpoint_to_dict,
    restore_report_to_dict,
)
from src.tools.checkpoints import CheckpointError

logger = logging.getLogger(__name__)

app = FastAPI(title="Pearl AI", version="1.2.0-beta")

# The workspace every session is built against. Set at startup.
_session: PearlSession | None = None
_workspace: Path | None = None
_UI_DIR = Path(__file__).resolve().parent.parent.parent / "pearl_ui"

# One session per user. With no auth configured every request resolves
# to LOCAL_USER, so a local run behaves exactly as it did when this was
# a single module-level session.
_registry = SessionRegistry()


def get_session(request: Request | None = None) -> PearlSession:
    """
    Return the calling user's session, creating it on first use.

    `request` is optional so the many existing call sites keep working:
    without it, the caller is the local user. Endpoints that must
    distinguish users pass it explicitly.
    """
    if _workspace is None:
        raise HTTPException(status_code=503, detail="Pearl session not initialized.")

    header = request.headers.get("authorization") if request is not None else None
    try:
        user_id = resolve_user(header)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # Single-user path: keep returning the session built at startup, so
    # nothing about a local run changes.
    if user_id == LOCAL_USER and _session is not None:
        return _session

    return _registry.get_or_create(  # type: ignore[return-value]
        user_id, lambda: PearlSession(_workspace)
    )


def init_session(workspace: Path) -> None:
    global _session, _workspace
    _workspace = workspace.resolve()
    _session = PearlSession(_workspace)


# ------------------------------------------------------------------ startup


@app.on_event("startup")
async def _startup() -> None:
    register_loop(asyncio.get_event_loop())

    # Initialise a session if nothing has already done so.
    #
    # `python -m src.api` calls init_session() itself before handing the
    # app to uvicorn, and that choice is preserved here. But running the
    # app directly — `uvicorn src.api.server:app`, which is how the
    # README says to start it and what any ASGI host would do — has no
    # such step, leaving _session as None so every endpoint behind
    # get_session() answers 503 while the UI shows "Disconnected".
    global _session, _workspace
    if _session is None:
        # _workspace must be set too: get_session() reads it to build
        # per-user sessions, and leaving it None reintroduces the 503
        # this block exists to prevent.
        _workspace = Path.cwd().resolve()
        logger.info("No workspace configured; defaulting to %s", _workspace)
        _session = PearlSession(_workspace)

    # Surface an unusable model configuration now rather than letting the
    # server look healthy and fail on every request.
    model_error = check_model_ready()
    if model_error:
        logger.warning("=" * 68)
        logger.warning("Pearl started, but the model cannot serve requests:")
        logger.warning("  %s", model_error)
        logger.warning("Chat, Agent and Code modes will all fail until this is fixed.")
        logger.warning("=" * 68)

    logger.info("Pearl API server started.")


# ------------------------------------------------------------------ UI


@app.get("/", response_class=HTMLResponse)
async def serve_ui() -> HTMLResponse:
    ui_file = _UI_DIR / "index.html"
    if not ui_file.exists():
        return HTMLResponse("<h1>Pearl UI not found</h1>", status_code=404)
    return HTMLResponse(ui_file.read_text(encoding="utf-8"))


# The browser loads the UI as ES modules, so index.html is no longer the
# whole front end and cannot be served on its own.
#
# Mounted at /js rather than at / so it cannot shadow an API route: a
# static mount at the root would answer before every /api/* handler
# below it.
_UI_JS_DIR = _UI_DIR / "js"

if _UI_JS_DIR.is_dir():
    app.mount("/js", StaticFiles(directory=_UI_JS_DIR), name="ui-js")
else:  # pragma: no cover - only in a truncated checkout
    logger.warning("Pearl UI modules not found at %s.", _UI_JS_DIR)


# ------------------------------------------------------------------ status


def check_model_ready() -> str | None:
    """
    Return why the configured model cannot serve requests, or None.

    A server whose interpreter lacks llama-cpp-python starts cleanly,
    serves the UI, and reports "Connected" — then fails every single
    request. Reporting that up front turns a healthy-looking server that
    silently does nothing into an obvious, explained problem.

    Only checks local inference: a remote provider's credentials cannot
    be validated without spending a real API call, which is not
    something a status poll should do.
    """
    from src.config.settings import Settings

    provider = Settings.LLM_PROVIDER.lower()
    if provider != "pearl" or Settings.PEARL_INFERENCE_API_KEY:
        return None  # remote — nothing checkable for free

    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        return (
            "llama-cpp-python is not importable in the Python running "
            f"Pearl ({sys.executable}). If it is installed elsewhere, this "
            "is the wrong interpreter — start Pearl with "
            "`python -m src.api`."
        )
    return None


@app.get("/api/status")
async def status(request: Request) -> JSONResponse:
    session = get_session(request)
    model_error = check_model_ready()
    return JSONResponse({
        "ok": True,
        "workspace": session.workspace_info(),
        "running": session.is_running(),
        "awaiting_approval": session.is_awaiting_approval(),
        # False means requests will fail; the UI should say so rather
        # than showing a healthy "Connected".
        "model_ready": model_error is None,
        "model_error": model_error,
    })


# ------------------------------------------------------------------ chat


class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    session = get_session(request)
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
async def run_autonomous(req: RunRequest, request: Request) -> StreamingResponse:
    session = get_session(request)

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
async def approve(request: Request) -> JSONResponse:
    session = get_session(request)
    result = session.approve()
    return JSONResponse(result)


@app.post("/api/reject")
async def reject(request: Request) -> JSONResponse:
    session = get_session(request)
    result = session.reject()
    return JSONResponse(result)


@app.post("/api/cancel")
async def cancel(request: Request) -> JSONResponse:
    session = get_session(request)
    cancelled = session.cancel()
    return JSONResponse({"cancelled": cancelled})


# ------------------------------------------------------------------ checkpoints


@app.get("/api/checkpoints")
async def checkpoints_list(
    request: Request,
    limit: int = Query(50, gt=0, description="Newest N checkpoints."),
) -> JSONResponse:
    """
    List this workspace's checkpoints, newest first.

    Includes the ones Pearl takes automatically before every approved
    write, which the browser has never been able to see.
    """
    session = get_session(request)
    checkpoints = session.checkpoints.list(limit=limit)
    return JSONResponse(
        {"checkpoints": [checkpoint_to_dict(c) for c in checkpoints]}
    )


def _refuse_on_shared_instance() -> None:
    """
    Refuse a checkpoint mutation when Pearl is serving more than one
    person.

    Checkpoints cannot have an ownership model here. The store is keyed
    by workspace path and `POST /api/workspace` already refuses under
    this same condition, so every user of a shared instance is pinned to
    one workspace and therefore one store. There are no per-user
    snapshots to own: one person restoring rolls back the files everyone
    else is working in.

    Reading stays open. Seeing what exists is how a user understands why
    the rest refuses.
    """
    if auth_required() or public_mode():
        raise HTTPException(
            status_code=403,
            detail=(
                "Changing checkpoints is disabled on a shared instance. "
                "Every user shares one workspace, so a restore would roll "
                "back everyone's files."
            ),
        )


class CreateCheckpointRequest(BaseModel):
    label: str = "Manual checkpoint"


class RenameCheckpointRequest(BaseModel):
    # Empty is rejected as a validation error rather than reaching the
    # store, which would accept it and leave a nameless checkpoint.
    label: str = Field(min_length=1)


@app.post("/api/checkpoints")
async def checkpoint_create(
    req: CreateCheckpointRequest, request: Request
) -> JSONResponse:
    """
    Snapshot the workspace on demand.

    The same store, and the same manager, that the executor checkpoints
    into automatically before writing.
    """
    _refuse_on_shared_instance()
    session = get_session(request)
    try:
        checkpoint = session.checkpoints.create(req.label)
    except CheckpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # None means nothing changed since the last snapshot. Reported as
    # null rather than invented, so the UI can say so.
    return JSONResponse(
        {
            "checkpoint": (
                checkpoint_to_dict(checkpoint) if checkpoint is not None else None
            )
        }
    )


@app.patch("/api/checkpoints/{checkpoint_id}")
async def checkpoint_rename(
    checkpoint_id: str, req: RenameCheckpointRequest, request: Request
) -> JSONResponse:
    """Change a checkpoint's display label."""
    _refuse_on_shared_instance()
    session = get_session(request)
    try:
        checkpoint = session.checkpoints.rename(checkpoint_id, req.label)
    except CheckpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"checkpoint": checkpoint_to_dict(checkpoint)})


@app.delete("/api/checkpoints/{checkpoint_id}")
async def checkpoint_delete(checkpoint_id: str, request: Request) -> JSONResponse:
    """
    Hide a checkpoint from listing and future restores.

    Never rewrites git history: see `CheckpointManager.delete`.
    """
    _refuse_on_shared_instance()
    session = get_session(request)
    try:
        session.checkpoints.delete(checkpoint_id)
    except CheckpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"deleted": True})


@app.post("/api/checkpoints/{checkpoint_id}/restore")
async def checkpoint_restore(checkpoint_id: str, request: Request) -> JSONResponse:
    """
    Restore the workspace to a checkpoint.

    Destructive: files created since the snapshot are removed. The
    browser calls the preview route and gets a confirmation before it
    calls this one.
    """
    _refuse_on_shared_instance()
    session = get_session(request)
    try:
        report = session.checkpoints.restore(checkpoint_id)
    except CheckpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(restore_report_to_dict(report))


@app.get("/api/checkpoints/{checkpoint_id}/restore-preview")
async def checkpoint_restore_preview(
    checkpoint_id: str, request: Request
) -> JSONResponse:
    """
    Report what restoring would change, without changing anything.

    Separate from the restore itself because restoring can delete files
    created since the snapshot. The browser shows this and asks, the
    same way Pearl shows a diff before writing.
    """
    session = get_session(request)
    try:
        report = session.checkpoints.preview_restore(checkpoint_id)
    except CheckpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(restore_report_to_dict(report))


# ------------------------------------------------------------------ patches


@app.get("/api/patches")
async def patches(request: Request) -> JSONResponse:
    session = get_session(request)
    return JSONResponse({
        "files": session.pending_files(),
        "diff": session.pending_diff(),
        "awaiting_approval": session.is_awaiting_approval(),
    })


# ------------------------------------------------------------------ history


@app.get("/api/history")
async def history(request: Request) -> JSONResponse:
    session = get_session(request)
    return JSONResponse({"messages": session.conversation_history()})


# ------------------------------------------------------------------ workspace


@app.get("/api/workspace")
async def get_workspace(request: Request) -> JSONResponse:
    session = get_session(request)
    return JSONResponse(session.workspace_info())


class WorkspaceRequest(BaseModel):
    path: str


@app.post("/api/workspace")
async def set_workspace(req: WorkspaceRequest) -> JSONResponse:
    """
    Repoint Pearl at another directory. Local use only.

    This accepts any path on the host, so on a reachable instance it is
    a full filesystem handle: POST {"path": "/"} and every file tool now
    operates on the whole server. That is acceptable for a tool running
    on your own laptop, where you already have that access, and is not
    acceptable anywhere else — so it is refused whenever the instance is
    shared (auth configured) or declared internet-facing.

    A hosted deployment gives each user a workspace it provisions; it
    does not let them choose one by absolute path.
    """
    global _session, _workspace

    if auth_required() or public_mode():
        raise HTTPException(
            status_code=403,
            detail=(
                "Changing the workspace is disabled on a shared instance. "
                "It would expose the whole host filesystem to any caller."
            ),
        )

    p = Path(req.path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")

    _workspace = p
    _session = PearlSession(p)
    # Other users' sessions were built against the old workspace.
    _registry.clear()
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


# ------------------------------------------------------------------ completion


class CompleteRequest(BaseModel):
    prefix: str
    suffix: str = ""
    language: str = ""


# One service per process: it owns the completion cache, so a per-request
# instance would make the cache useless.
_completion_service = CompletionService()


@app.post("/api/complete")
async def complete(req: CompleteRequest) -> JSONResponse:
    """
    Return one inline completion for the text at a cursor position.

    On the typing path, so it never returns an error status for a model
    failure — an unavailable model yields an empty completion with a
    reason. A caller polling this on every keystroke must not have to
    handle exceptions to keep working.

    Runs in a worker thread: local inference is a blocking CPU call, and
    holding the event loop through it would stall every other request.
    """
    result = await asyncio.to_thread(
        _completion_service.complete,
        req.prefix,
        req.suffix,
        req.language,
    )
    return JSONResponse({
        "completion": result.text,
        "cached": result.cached,
        "declined_reason": result.declined_reason,
    })


# ------------------------------------------------------------------ tools


@app.get("/api/tools")
async def tools(request: Request) -> JSONResponse:
    session = get_session(request)
    tool_list = [
        {"name": t["name"], "description": t.get("description", "")}
        for t in session.registry.get_tools()
    ]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})


# ------------------------------------------------------------------ sessions (V2)



class RenameSessionRequest(BaseModel):
    title: str


@app.get("/api/sessions")
async def list_sessions() -> JSONResponse:
    """List all persistent sessions, newest first."""
    manager = get_session_manager()
    return JSONResponse({"sessions": manager.list_sessions()})


@app.post("/api/sessions")
async def create_session_endpoint(request: Request) -> JSONResponse:
    """Create a new persistent session for the current workspace."""
    session = get_session(request)
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
