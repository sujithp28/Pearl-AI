"""
Persistent session manager for Pearl V2.

Sessions survive application restart.  Each session is stored as a JSON
file in ``~/.pearl/sessions/<session_id>.json``.  No external database
is required.

Session isolation is enforced: loading session A never affects session B.

Public API::

    manager = SessionManager()

    sid = manager.create_session(workspace="/path/to/project")
    manager.append_message(sid, role="user", content="fix the bug")
    session = manager.get_session(sid)
    sessions = manager.list_sessions()
    manager.rename_session(sid, "Auth bugfix")
    manager.delete_session(sid)
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

_SESSIONS_DIR = Path.home() / ".pearl" / "sessions"


def _now() -> str:
    # timespec="microseconds" avoids the 15ms timer-resolution flake on Windows.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class SessionMessage:
    role: str      # "user" | "assistant" | "summary"
    content: str
    ts: str = field(default_factory=_now)


@dataclass
class SessionRecord:
    id: str
    title: str
    workspace: str
    created_at: str
    updated_at: str
    messages: list[SessionMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        messages = [SessionMessage(**m) for m in data.pop("messages", [])]
        rec = cls(**data)
        rec.messages = messages
        return rec

    @property
    def summary_dict(self) -> dict[str, Any]:
        """Return lightweight metadata (no messages) for list endpoints."""
        return {
            "id": self.id,
            "title": self.title,
            "workspace": self.workspace,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "metadata": self.metadata,
        }


class SessionManager:
    """
    Manages persistent Pearl sessions stored as JSON files.

    Thread-safe for concurrent read/write from SSE/worker threads.
    """

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self._dir = (sessions_dir or _SESSIONS_DIR).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()  # reentrant: read-modify-write callers can hold across _save()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create_session(
        self,
        workspace: str,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new session and return its ID."""
        sid = str(uuid.uuid4())
        now = _now()
        record = SessionRecord(
            id=sid,
            title=title or f"Session {sid[:8]}",
            workspace=workspace,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._save(record)
        logger.info("SessionManager: created session %s workspace=%s", sid, workspace)
        return sid

    def get_session(self, session_id: str) -> SessionRecord:
        """Load and return a session.  Raises KeyError if not found."""
        path = self._path(session_id)
        if not path.exists():
            raise KeyError(f"Session not found: {session_id}")
        with self._lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        return SessionRecord.from_dict(data)

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return session summaries sorted newest-first."""
        summaries: list[dict[str, Any]] = []
        with self._lock:
            for path in self._dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    rec = SessionRecord.from_dict(data)
                    summaries.append(rec.summary_dict)
                except Exception:
                    logger.warning("SessionManager: corrupt session file %s", path)
        summaries.sort(key=lambda s: s["updated_at"], reverse=True)
        return summaries

    def delete_session(self, session_id: str) -> None:
        """Delete a session.  No-op if already gone."""
        path = self._path(session_id)
        with self._lock:
            path.unlink(missing_ok=True)
        logger.info("SessionManager: deleted session %s", session_id)

    def rename_session(self, session_id: str, title: str) -> None:
        """Rename a session."""
        with self._lock:
            rec = self.get_session(session_id)
            rec.title = title
            rec.updated_at = _now()
            self._save(rec)

    # ── Messages ─────────────────────────────────────────────────────────────

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """Append one message to the session.  Thread-safe (RLock guards full read-modify-write)."""
        with self._lock:
            path = self._path(session_id)
            if not path.exists():
                raise KeyError(f"Session not found: {session_id}")
            data = json.loads(path.read_text(encoding="utf-8"))
            rec = SessionRecord.from_dict(data)
            rec.messages.append(SessionMessage(role=role, content=content))
            rec.updated_at = _now()
            self._save(rec)

    def load_history(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        """
        Return conversation messages in ``[{role, content}]`` format
        suitable for LLM APIs, with internal roles translated.
        """
        rec = self.get_session(session_id)
        msgs = rec.messages if limit is None else rec.messages[-limit:]
        result: list[dict[str, str]] = []
        for m in msgs:
            api_role = "assistant" if m.role in ("agent", "summary") else m.role
            result.append({"role": api_role, "content": m.content})
        return result

    def update_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        """Merge `metadata` into the session's metadata dict."""
        with self._lock:
            rec = self.get_session(session_id)
            rec.metadata.update(metadata)
            rec.updated_at = _now()
            self._save(rec)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _path(self, session_id: str) -> Path:
        # Sanitize: only allow UUID-like IDs to prevent path traversal.
        safe = "".join(c for c in session_id if c.isalnum() or c == "-")
        if safe != session_id:
            raise ValueError(f"Invalid session ID: {session_id!r}")
        return self._dir / f"{safe}.json"

    def _save(self, record: SessionRecord) -> None:
        path = self._path(record.id)
        content = json.dumps(record.to_dict(), indent=2, default=str)
        with self._lock:
            path.write_text(content, encoding="utf-8")


# ── Module-level default instance ────────────────────────────────────────────

_default_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = SessionManager()
    return _default_manager
