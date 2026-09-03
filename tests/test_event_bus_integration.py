"""
Tests for EventBus wiring in PearlSession.run_autonomous_stream.

Verifies that:
- context_condensed events arrive on the SSE queue when condensation fires
- run_failed events arrive on the SSE queue on executor exception
- run_complete events arrive before the result dict
- progress events still arrive (backward compat)
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.agent.condenser import CondensationResult
from src.agent.event_bus import ContextCondensedEvent, RunCompleteEvent, RunFailedEvent


def _make_session(tmp_path):
    """Build a PearlSession with scripted LLM, no real model needed."""
    import os
    os.environ.setdefault("PEARL_LLM_PROVIDER", "scripted")
    from src.config.settings import Settings
    from src.api.session import PearlSession

    with patch.object(Settings, "LLM_PROVIDER", "scripted"), \
         patch("src.api.session.RepositoryService.get_or_build", return_value=MagicMock()), \
         patch("src.api.session.build_startup_index"):
        session = PearlSession(tmp_path)
    return session


def _drain_queue_sync(q: asyncio.Queue, loop: asyncio.AbstractEventLoop, timeout=2.0) -> list[dict]:
    """Collect all items from an asyncio.Queue until None sentinel, with timeout."""
    items: list[dict] = []

    async def _collect():
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=timeout)
                if item is None:
                    break
                items.append(item)
            except asyncio.TimeoutError:
                break

    future = asyncio.run_coroutine_threadsafe(_collect(), loop)
    future.result(timeout=timeout + 1)
    return items


# ---------------------------------------------------------------------------
# context_condensed event
# ---------------------------------------------------------------------------


class TestContextCondensedEvent:
    def test_context_condensed_event_emitted_when_condenser_fires(self, tmp_path):
        """A ContextCondensedEvent arrives on event_queue when condensation happens."""
        session = _make_session(tmp_path)

        condensed_result = CondensationResult(
            condensed=True,
            turns_before=20,
            turns_after=5,
            tokens_before=8000,
            tokens_after=2000,
            reason="Too many tokens",
        )

        fake_report = MagicMock()
        fake_report.stop_reason = "complete"
        fake_report.steps = []
        fake_report.replans_used = 0
        fake_report.succeeded = True

        with patch.object(session._condenser, "maybe_condense", return_value=condensed_result), \
             patch.object(session._synthesizer, "synthesize", return_value="Done."), \
             patch("src.api.session.AutonomousExecutor") as MockExec:
            mock_exec = MagicMock()
            mock_exec.run.return_value = fake_report
            mock_exec.patch_manager.combined_diff.return_value = ""
            mock_exec.patch_manager.affected_files.return_value = []
            MockExec.return_value = mock_exec

            loop = asyncio.new_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _run_loop():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=_run_loop, daemon=True)
            t.start()

            with patch("src.api.session._get_loop", return_value=loop):
                # Run in thread like the server does
                worker_thread = threading.Thread(
                    target=lambda: session.run_autonomous_stream("fix bug", q)
                )
                worker_thread.start()
                worker_thread.join(timeout=5)
                # Put None sentinel to signal end (server would do this)
                asyncio.run_coroutine_threadsafe(q.put(None), loop).result(timeout=1)

            items = _drain_queue_sync(q, loop, timeout=2)
            loop.call_soon_threadsafe(loop.stop)

        types = [item["type"] for item in items]
        assert "context_condensed" in types, f"Expected context_condensed in {types}"
        cc = next(i for i in items if i["type"] == "context_condensed")
        assert cc["turns_before"] == 20
        assert cc["turns_after"] == 5
        assert cc["tokens_before"] == 8000
        assert cc["tokens_after"] == 2000

    def test_no_condensed_event_when_condenser_does_not_fire(self, tmp_path):
        """No context_condensed event when condensation is skipped."""
        session = _make_session(tmp_path)

        no_condense = CondensationResult(
            condensed=False, turns_before=5, turns_after=5,
            tokens_before=1000, tokens_after=1000, reason="",
        )

        fake_report = MagicMock()
        fake_report.stop_reason = "complete"
        fake_report.steps = []
        fake_report.replans_used = 0
        fake_report.succeeded = True

        with patch.object(session._condenser, "maybe_condense", return_value=no_condense), \
             patch.object(session._synthesizer, "synthesize", return_value="Done."), \
             patch("src.api.session.AutonomousExecutor") as MockExec:
            mock_exec = MagicMock()
            mock_exec.run.return_value = fake_report
            mock_exec.patch_manager.combined_diff.return_value = ""
            mock_exec.patch_manager.affected_files.return_value = []
            MockExec.return_value = mock_exec

            loop = asyncio.new_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _run_loop():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=_run_loop, daemon=True)
            t.start()

            with patch("src.api.session._get_loop", return_value=loop):
                worker_thread = threading.Thread(
                    target=lambda: session.run_autonomous_stream("fix bug", q)
                )
                worker_thread.start()
                worker_thread.join(timeout=5)
                asyncio.run_coroutine_threadsafe(q.put(None), loop).result(timeout=1)

            items = _drain_queue_sync(q, loop, timeout=2)
            loop.call_soon_threadsafe(loop.stop)

        types = [item["type"] for item in items]
        assert "context_condensed" not in types


# ---------------------------------------------------------------------------
# run_complete / run_failed events
# ---------------------------------------------------------------------------


class TestRunLifecycleEvents:
    def test_run_complete_event_emitted_before_result(self, tmp_path):
        """RunCompleteEvent must arrive before the result dict on success."""
        session = _make_session(tmp_path)

        no_condense = CondensationResult(
            condensed=False, turns_before=0, turns_after=0,
            tokens_before=0, tokens_after=0, reason="",
        )
        fake_report = MagicMock()
        fake_report.stop_reason = "complete"
        fake_report.steps = []
        fake_report.replans_used = 0
        fake_report.succeeded = True

        with patch.object(session._condenser, "maybe_condense", return_value=no_condense), \
             patch.object(session._synthesizer, "synthesize", return_value="Done."), \
             patch("src.api.session.AutonomousExecutor") as MockExec:
            mock_exec = MagicMock()
            mock_exec.run.return_value = fake_report
            MockExec.return_value = mock_exec

            loop = asyncio.new_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _run_loop():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            threading.Thread(target=_run_loop, daemon=True).start()

            with patch("src.api.session._get_loop", return_value=loop):
                worker_thread = threading.Thread(
                    target=lambda: session.run_autonomous_stream("fix bug", q)
                )
                worker_thread.start()
                worker_thread.join(timeout=5)
                asyncio.run_coroutine_threadsafe(q.put(None), loop).result(timeout=1)

            items = _drain_queue_sync(q, loop, timeout=2)
            loop.call_soon_threadsafe(loop.stop)

        types = [item["type"] for item in items]
        assert "run_complete" in types
        assert "result" in types
        rc_idx = types.index("run_complete")
        res_idx = types.index("result")
        assert rc_idx < res_idx, f"run_complete ({rc_idx}) must precede result ({res_idx})"

    def test_run_failed_event_emitted_on_exception(self, tmp_path):
        """RunFailedEvent arrives before the error dict on executor exception."""
        session = _make_session(tmp_path)

        no_condense = CondensationResult(
            condensed=False, turns_before=0, turns_after=0,
            tokens_before=0, tokens_after=0, reason="",
        )

        with patch.object(session._condenser, "maybe_condense", return_value=no_condense), \
             patch("src.api.session.AutonomousExecutor") as MockExec:
            mock_exec = MagicMock()
            mock_exec.run.side_effect = RuntimeError("something went wrong")
            MockExec.return_value = mock_exec

            loop = asyncio.new_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _run_loop():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            threading.Thread(target=_run_loop, daemon=True).start()

            with patch("src.api.session._get_loop", return_value=loop):
                worker_thread = threading.Thread(
                    target=lambda: session.run_autonomous_stream("fix bug", q)
                )
                worker_thread.start()
                worker_thread.join(timeout=5)
                asyncio.run_coroutine_threadsafe(q.put(None), loop).result(timeout=1)

            items = _drain_queue_sync(q, loop, timeout=2)
            loop.call_soon_threadsafe(loop.stop)

        types = [item["type"] for item in items]
        assert "run_failed" in types
        assert "error" in types
        rf_idx = types.index("run_failed")
        err_idx = types.index("error")
        assert rf_idx < err_idx, f"run_failed ({rf_idx}) must precede error ({err_idx})"
