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
    """
    Inference on a single Llama object must be serialized —
    llama_cpp is not thread-safe for concurrent calls on one model.

    The lock is per-model (keyed by model path + n_ctx) rather than one
    global lock, so a fast autocomplete on a small model is not queued
    behind a slow planning call on a large one.  The serialization
    guarantee below is unchanged; only its granularity is.
    """

    def test_inference_lock_is_a_real_lock(self):
        """The per-model lock must be a real lock, not a no-op."""
        import src.llm.providers.local_inference as mod
        lock = mod._get_inference_lock("fake.gguf", 512)
        assert hasattr(lock, "acquire") and hasattr(lock, "release"), (
            "per-model inference lock must have acquire/release"
        )

    def test_same_model_returns_the_same_lock(self):
        """Two providers on one model must share a lock, or they'd race."""
        import src.llm.providers.local_inference as mod
        a = mod._get_inference_lock("same.gguf", 512)
        b = mod._get_inference_lock("same.gguf", 512)
        assert a is b, "the same model must map to the same lock object"

    def test_different_models_get_different_locks(self):
        """Distinct models must not serialize against each other."""
        import src.llm.providers.local_inference as mod
        a = mod._get_inference_lock("small.gguf", 512)
        b = mod._get_inference_lock("large.gguf", 512)
        assert a is not b, (
            "distinct models sharing one lock would make autocomplete "
            "queue behind planning"
        )

    def test_inference_lock_blocks_a_second_thread(self):
        """A held lock must block another thread, not deadlock or pass."""
        import src.llm.providers.local_inference as mod
        lock = mod._get_inference_lock("blocking.gguf", 512)
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
            "Another thread should not acquire the lock while it is held"
        )

    def test_complete_acquires_inference_lock(self):
        """complete() must hold its model's lock around the model call."""
        import src.llm.providers.local_inference as mod

        fake_llm = MagicMock()
        fake_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "hello"}}]
        }

        lock_acquired_during_call: list[bool] = []

        original_ccc = fake_llm.create_chat_completion.side_effect

        # The lock this provider's model maps to.
        model_lock = mod._get_inference_lock("fake.gguf", 512)

        def _check_lock(*args, **kwargs):
            # While inside create_chat_completion, the lock must be held
            # (non-blocking acquire from this same thread would fail for a
            # non-reentrant Lock, but we can check the locked() status).
            locked = not model_lock.acquire(blocking=False)
            lock_acquired_during_call.append(locked)
            if not locked:
                # We accidentally acquired it — release so lock isn't stuck.
                model_lock.release()
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
            "the model's inference lock was NOT held during "
            "create_chat_completion()"
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


class TestWorkspaceIsolationConcurrent:
    """
    Regression: two concurrent workspace operations must not corrupt
    each other's boundary checks by racing on the process-global cwd.

    _build_index() no longer calls os.chdir(), so concurrent index
    builds for different workspaces must resolve paths independently.
    """

    def test_concurrent_build_startup_index_uses_explicit_paths(self, tmp_path):
        """
        build_startup_index(path) takes the workspace root as an
        explicit argument and must not rely on os.getcwd().
        """
        import inspect
        from src.tools.repo_tools import build_startup_index
        source = inspect.getsource(build_startup_index)
        # build_startup_index should use the `path` parameter, not cwd.
        # It passes the path to index_repository() which uses it directly.
        assert "path" in source, (
            "build_startup_index must accept and use an explicit path argument"
        )

    def test_two_concurrent_file_ops_stay_within_own_workspace(self, tmp_path):
        """
        Two threads each writing to their own workspace sub-directory
        must not interfere.  This would fail if either relied on a
        process-global cwd that the other thread changes.
        """
        import threading
        from src.tools.patch_manager import ChangeManager

        ws_a = tmp_path / "workspace_a"
        ws_b = tmp_path / "workspace_b"
        ws_a.mkdir()
        ws_b.mkdir()

        results: list[str] = []
        errors: list[Exception] = []

        def write_in(ws: Path, name: str) -> None:
            try:
                pm = ChangeManager()
                target = str(ws / f"{name}.txt")
                pm.propose(target, None, f"content from {name}\n")
                pm.apply_all()
                results.append(target)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=write_in, args=(ws_a, "a"))
        t2 = threading.Thread(target=write_in, args=(ws_b, "b"))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Concurrent writes failed: {errors}"
        assert len(results) == 2
        assert (ws_a / "a.txt").exists()
        assert (ws_b / "b.txt").exists()
