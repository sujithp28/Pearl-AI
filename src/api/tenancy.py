"""
Per-user sessions for a hosted Pearl.

Pearl was built as a local single-user tool, and `src/api/server.py`
said so: one module-level `PearlSession` shared by every request. That
is correct for a laptop and unusable when more than one person connects
— two users would share one conversation, one workspace, and one
approval queue, so either could approve the other's file writes.

This module makes the session per-caller without changing what a local
run does. With no auth configured, every request resolves to the same
built-in local user, which is exactly the previous behaviour; the
multi-tenant path only engages once tokens exist.

What this does NOT do
---------------------
Isolate *execution*. Sessions are separate, but tools still run as the
server process on the server's filesystem, and `execute_shell` still
runs real commands. Serving untrusted users additionally requires a
sandbox per user (a container, or a VM) — that is a deployment
decision, not something this layer can fake. `PUBLIC_MODE` exists to
make refusing that combination explicit rather than accidental.
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: The single user a local, unauthenticated Pearl runs as.
LOCAL_USER = "local"

#: Sessions hold a repository index and LLM clients, so each costs real
#: memory. Bounded, with least-recently-used eviction, so a burst of
#: visitors cannot exhaust the host.
DEFAULT_MAX_SESSIONS = 50

#: Idle sessions are dropped rather than kept forever.
DEFAULT_IDLE_TIMEOUT_SECONDS = 60 * 60


class AuthenticationError(Exception):
    """Raised when a request carries no valid identity."""


@dataclass
class _Entry:
    session: object  # PearlSession — untyped to avoid an import cycle
    last_used: float = field(default_factory=time.monotonic)


class SessionRegistry:
    """
    Maps a user id to that user's `PearlSession`.

    Thread-safe: the HTTP server serves requests from a pool, and two
    requests from the same user must resolve to the same session rather
    than racing to build two.
    """

    def __init__(
        self,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self._sessions: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max(1, max_sessions)
        self._idle_timeout = idle_timeout

    def get_or_create(self, user_id: str, factory) -> object:
        """
        Return `user_id`'s session, building it via `factory()` if needed.

        `factory` is called with the lock held. That serialises session
        creation, which is deliberate: building one loads a repository
        index, and two concurrent requests from the same user would
        otherwise each build their own and discard one.
        """
        with self._lock:
            self._evict_idle_locked()

            entry = self._sessions.get(user_id)
            if entry is not None:
                entry.last_used = time.monotonic()
                self._sessions.move_to_end(user_id)
                return entry.session

            session = factory()
            self._sessions[user_id] = _Entry(session=session)
            self._sessions.move_to_end(user_id)
            logger.info("Created session for user %r", user_id)

            self._evict_over_capacity_locked()
            return session

    def peek(self, user_id: str) -> object | None:
        """Return an existing session without creating one."""
        with self._lock:
            entry = self._sessions.get(user_id)
            return entry.session if entry else None

    def drop(self, user_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(user_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    # ── Internals (call with the lock held) ──────────────────────────────

    def _evict_idle_locked(self) -> None:
        if self._idle_timeout <= 0:
            return
        cutoff = time.monotonic() - self._idle_timeout
        stale = [uid for uid, e in self._sessions.items() if e.last_used < cutoff]
        for uid in stale:
            # Never evict a run that is mid-flight or waiting on the user
            # to approve a diff — dropping it would silently discard
            # staged changes they are still looking at.
            if self._is_busy(self._sessions[uid].session):
                continue
            self._sessions.pop(uid, None)
            logger.info("Evicted idle session for user %r", uid)

    def _evict_over_capacity_locked(self) -> None:
        while len(self._sessions) > self._max:
            for uid in list(self._sessions):
                if self._is_busy(self._sessions[uid].session):
                    continue
                self._sessions.pop(uid, None)
                logger.warning(
                    "Session cap (%d) reached; evicted least-recently-used "
                    "user %r",
                    self._max,
                    uid,
                )
                break
            else:
                # Everything is busy. Going over cap beats cancelling
                # someone's in-flight run.
                logger.warning(
                    "Session cap (%d) exceeded but all sessions are busy.",
                    self._max,
                )
                return

    @staticmethod
    def _is_busy(session: object) -> bool:
        try:
            return bool(
                session.is_running() or session.is_awaiting_approval()  # type: ignore[attr-defined]
            )
        except Exception:
            return False


# ── Identity ─────────────────────────────────────────────────────────────


def auth_required() -> bool:
    """
    Whether requests must carry a token.

    Off by default so a local run needs no configuration, and so this
    change cannot silently lock someone out of their own laptop.
    """
    return bool(os.getenv("PEARL_AUTH_TOKENS", "").strip())


def _token_table() -> dict[str, str]:
    """
    Parse ``PEARL_AUTH_TOKENS`` into ``{token: user_id}``.

    Format: ``token1:alice,token2:bob``. A real deployment would use a
    database; the point here is that identity is resolved in one place,
    so replacing this function is the whole change.
    """
    raw = os.getenv("PEARL_AUTH_TOKENS", "")
    table: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        token, _, user = pair.partition(":")
        token, user = token.strip(), user.strip()
        if token and user:
            table[token] = user
    return table


def resolve_user(authorization: str | None) -> str:
    """
    Return the caller's user id.

    Without auth configured this is always `LOCAL_USER`, preserving
    single-user behaviour exactly.

    Raises
    ------
    AuthenticationError
        When auth is configured and the request carries no valid token.
    """
    if not auth_required():
        return LOCAL_USER

    if not authorization:
        raise AuthenticationError("Missing Authorization header.")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("Expected 'Authorization: Bearer <token>'.")

    # Compared with compare_digest against every entry rather than a dict
    # lookup: a plain lookup leaks token validity through timing.
    for known, user in _token_table().items():
        if hmac.compare_digest(token, known):
            return user

    raise AuthenticationError("Invalid token.")


def public_mode() -> bool:
    """
    Whether the operator has declared this instance internet-facing.

    Serving untrusted users needs a sandbox per user, because tools run
    real commands on the server. Pearl cannot verify a sandbox exists,
    so this flag is the operator asserting it — and its absence is what
    lets dangerous endpoints refuse by default instead of relying on
    nobody finding them.
    """
    return os.getenv("PEARL_PUBLIC_MODE", "").lower() in ("1", "true", "yes")
