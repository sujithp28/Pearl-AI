"""
Tests for Milestone 2 concurrency safety — G3 (inference lock) and G4 (chdir fix).

The inference lock test verifies that _inference_lock is a module-level
threading.Lock and that complete() acquires it around create_chat_completion().
The chdir test verifies that chat_stream() no longer calls os.chdir().
"""

from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock, patch

import pytest


class TestInferenceLock:
    def test_inference_lock_is_module_level_threading_lock(self):
        """_inference_lock must be a real threading.Lock, not a no-op."""
        import src.llm.providers.local_inference as mod
        assert hasattr(mod, "_inference_lock"), (
            "_inference_lock missing from local_inference module"
        )
        lock = mod._inference_lock
        # threading.Lock() returns a _thread.lock; both Lock and RLock are fine.
        assert hasattr(lock, "acquire") and hasattr(lock, "release"), (
            "_inference_lock must be a lock-like object with acquire/release"
        )

    def test_inference_lock_is_reentrant_safe(self):
        """Acquiring _inference_lock from a different thread must block, not deadlock."""
        import src.llm.providers.local_inference as mod
        lock = mod._inference_lock
        results: list[str] = []

        def _try_acquire() -> None:
            # Non-blocking attempt — if lock is held this should fail.
            acquired = lock.acquire(blocking=False)
            results.append("acquired" if acquired else "blocked")
            if acquired:
                lock.release()

        with lock:
            t = threading.Thread(target=_try_acquire)
            t.start()
            t.join(timeout=2.0)

        assert results == ["blocked"], (
            "Another thread should not acquire _inference_lock while it is held"
        )

    def test_complete_acquires_inference_lock(self):
        """complete() must hold _inference_lock around create_chat_completion()."""
        import src.llm.providers.local_inference as mod

        fake_llm = MagicMock()
        fake_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "hello"}}]
        }

        lock_acquired_during_call: list[bool] = []

        original_ccc = fake_llm.create_chat_completion.side_effect

        def _check_lock(*args, **kwargs):
            # While inside create_chat_completion, the lock must be held
            # (non-blocking acquire from this same thread would fail for a
            # non-reentrant Lock, but we can check the locked() status).
            locked = not mod._inference_lock.acquire(blocking=False)
            lock_acquired_during_call.append(locked)
            if not locked:
                # We accidentally acquired it — release so lock isn't stuck.
                mod._inference_lock.release()
            return {"choices": [{"message": {"content": "hello"}}]}

        fake_llm.create_chat_completion.side_effect = _check_lock

        provider = mod.LocalInferenceProvider.__new__(mod.LocalInferenceProvider)
        provider._ready = threading.Event()
        provider._ready.set()
        provider._error = None
        provider._model_path = "fake.gguf"
        provider._repo_id = ""
        provider._filename = ""
        provider._n_ctx = 512
        provider._n_threads = 1

        with patch.object(mod, "_get_shared_llm", return_value=fake_llm):
            provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.0,
                max_tokens=16,
            )

        assert lock_acquired_during_call, "check did not run"
        assert lock_acquired_during_call[0], (
            "_inference_lock was NOT held during create_chat_completion()"
        )


class TestChatStreamNoChdirG4:
    def test_chat_stream_does_not_call_os_chdir(self):
        """
        G4: chat_stream() must not call os.chdir() — it races with
        run_autonomous_stream() which also changes cwd.
        """
        from src.api.session import PearlSession
        source = inspect.getsource(PearlSession.chat_stream)
        assert "os.chdir" not in source, (
            "chat_stream() still calls os.chdir() — remove it (G4 fix)"
        )
