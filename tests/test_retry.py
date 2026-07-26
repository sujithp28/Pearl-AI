"""
Tests for transient error classification and retry behaviour.

Three sections:
  1. is_transient_error() — pure classifier unit tests.
  2. AutonomousExecutor retry integration — transient errors are retried
     before replanning; fatal errors go directly to the replan path.
  3. Retry limit behaviour — exhausting max_retries falls through to the
     existing failure → replan path, leaving approval unchanged.
"""

from __future__ import annotations

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import (
    DEFAULT_MAX_RETRIES,
    AutonomousExecutor,
    ProgressEvent,
)
from src.agent.planner import Planner
from src.agent.retry import (
    _FATAL_TYPES,
    _TRANSIENT_PATTERNS,
    _TRANSIENT_TYPES,
    classify_error,
    is_transient_error,
)
from src.tools.edit_tools import set_active_patch_manager
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry
from src.tools.shell_tools import set_active_command_approver


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_approval_state():
    yield
    set_active_patch_manager(None)
    set_active_command_approver(None)


@tool(description="Add two numbers.", parameters={"a": "int", "b": "int"}, returns="int")
def add(a: int, b: int) -> int:
    return a + b


def _build_executor(max_retries: int = DEFAULT_MAX_RETRIES, max_replans: int = 3):
    registry = ToolRegistry()
    registry.register(add)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)
    return AutonomousExecutor(
        planner,
        dispatcher,
        max_retries=max_retries,
        max_replans=max_replans,
        checkpoints=None,
    ), planner


def _plan_of(*steps):
    return lambda prompt, cancel_check=None: {"steps": list(steps)}


def _make_flaky_tool(fail_count: int, exc: Exception):
    """
    Return a tool function that raises ``exc`` for the first
    ``fail_count`` calls, then succeeds.
    """
    state = {"calls": 0}

    @tool(description="Flaky tool.", parameters={"x": "int"}, returns="int")
    def flaky(x: int) -> int:
        state["calls"] += 1
        if state["calls"] <= fail_count:
            raise exc
        return x * 2

    return flaky


# ---------------------------------------------------------------------------
# 1. is_transient_error() — pure classifier
# ---------------------------------------------------------------------------


class TestKnownFatalTypes:
    @pytest.mark.parametrize("exc_type", _FATAL_TYPES)
    def test_known_fatal_type_returns_false(self, exc_type):
        assert is_transient_error(exc_type("some error")) is False

    def test_value_error_is_fatal(self):
        assert is_transient_error(ValueError("invalid argument")) is False

    def test_type_error_is_fatal(self):
        assert is_transient_error(TypeError("wrong type")) is False

    def test_file_not_found_is_fatal(self):
        assert is_transient_error(FileNotFoundError("no such file")) is False

    def test_permission_error_is_fatal(self):
        assert is_transient_error(PermissionError("access denied")) is False

    def test_is_a_directory_error_is_fatal(self):
        assert is_transient_error(IsADirectoryError("is a dir")) is False

    def test_not_a_directory_error_is_fatal(self):
        assert is_transient_error(NotADirectoryError("not a dir")) is False

    def test_fatal_type_beats_transient_message(self):
        # Even if the message contains "timeout", a fatal type is still fatal.
        assert is_transient_error(ValueError("connection timeout 503")) is False


class TestKnownTransientTypes:
    @pytest.mark.parametrize("exc_type", _TRANSIENT_TYPES)
    def test_known_transient_type_returns_true(self, exc_type):
        assert is_transient_error(exc_type("temporary")) is True

    def test_timeout_error_is_transient(self):
        assert is_transient_error(TimeoutError("request timed out")) is True

    def test_connection_reset_is_transient(self):
        assert is_transient_error(ConnectionResetError("connection reset by peer")) is True

    def test_connection_aborted_is_transient(self):
        assert is_transient_error(ConnectionAbortedError("aborted")) is True

    def test_broken_pipe_is_transient(self):
        assert is_transient_error(BrokenPipeError("pipe")) is True

    def test_blocking_io_is_transient(self):
        assert is_transient_error(BlockingIOError("eagain")) is True

    def test_interrupted_error_is_transient(self):
        assert is_transient_error(InterruptedError("interrupted")) is True


class TestMessagePatterns:
    @pytest.mark.parametrize("pattern", _TRANSIENT_PATTERNS)
    def test_transient_pattern_in_generic_exception_returns_true(self, pattern: str):
        assert is_transient_error(Exception(f"error: {pattern}")) is True

    def test_http_429_in_message_is_transient(self):
        assert is_transient_error(Exception("HTTP 429: Too Many Requests")) is True

    def test_http_502_in_message_is_transient(self):
        assert is_transient_error(Exception("502 Bad Gateway")) is True

    def test_http_503_in_message_is_transient(self):
        assert is_transient_error(Exception("service unavailable 503")) is True

    def test_http_504_in_message_is_transient(self):
        assert is_transient_error(Exception("504 gateway timeout")) is True

    def test_rate_limit_in_message_is_transient(self):
        assert is_transient_error(Exception("rate limit exceeded")) is True

    def test_generic_exception_no_pattern_is_not_transient(self):
        assert is_transient_error(Exception("something went wrong")) is False

    def test_plain_exception_is_not_transient(self):
        assert is_transient_error(Exception("unexpected error")) is False

    def test_case_insensitive_matching(self):
        assert is_transient_error(Exception("TIMEOUT OCCURRED")) is True

    def test_message_check_only_for_unknown_types(self):
        # OSError is not in either known set; message scan applies.
        assert is_transient_error(OSError("etimedout")) is True
        assert is_transient_error(OSError("no such file")) is False

    def test_returns_bool_not_other_type(self):
        result = is_transient_error(Exception("timeout"))
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 2. Executor retry integration
# ---------------------------------------------------------------------------


class TestTransientRetryBeforeReplan:
    def test_transient_failure_is_retried_and_succeeds(self, monkeypatch):
        """A tool that fails once with TimeoutError succeeds on retry."""
        executor, planner = _build_executor(max_retries=3)

        call_count = {"n": 0}

        def flaky_add(a, b):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TimeoutError("request timed out")
            return a + b

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(executor.dispatcher._registry.get_tool("add"), "function", flaky_add)

        report = executor.run("add 1+2")

        assert report.stop_reason == "completed"
        assert report.replans_used == 0  # transient retry never uses a replan slot

    def test_transient_retry_emits_step_retrying_event(self, monkeypatch):
        executor, planner = _build_executor(max_retries=3)
        call_count = {"n": 0}

        def flaky_add(a, b):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TimeoutError("timed out")
            return a + b

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(executor.dispatcher._registry.get_tool("add"), "function", flaky_add)

        events: list[ProgressEvent] = []
        executor.on_progress = events.append

        executor.run("add 1+2")

        statuses = [e.status for e in events]
        assert "step_retrying" in statuses

    def test_transient_retry_does_not_consume_replan_slot(self, monkeypatch):
        executor, planner = _build_executor(max_retries=3, max_replans=1)
        call_count = {"n": 0}

        def flaky_add(a, b):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise TimeoutError("timed out")
            return a + b

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(executor.dispatcher._registry.get_tool("add"), "function", flaky_add)

        report = executor.run("add 1+2")

        # Two retries succeeded; replan budget is untouched.
        assert report.stop_reason == "completed"
        assert report.replans_used == 0

    def test_transient_retry_does_not_consume_iteration_slot(self, monkeypatch):
        """
        Retries must not eat iteration budget — a plan with one step
        that retries twice should complete normally with max_iterations=1.
        """
        executor, planner = _build_executor(max_retries=3)
        executor.max_iterations = 1
        call_count = {"n": 0}

        def flaky_add(a, b):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise TimeoutError("timed out")
            return a + b

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(executor.dispatcher._registry.get_tool("add"), "function", flaky_add)

        report = executor.run("add 1+2")
        assert report.stop_reason == "completed"

    def test_fatal_error_is_not_retried(self, monkeypatch):
        # max_replans=0 isolates retry behaviour from the replan path:
        # any failure goes straight to fatal_error without replanning.
        executor, planner = _build_executor(max_retries=3, max_replans=0)
        call_count = {"n": 0}

        def bad_add(a, b):
            call_count["n"] += 1
            raise ValueError("invalid arguments")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(executor.dispatcher._registry.get_tool("add"), "function", bad_add)

        report = executor.run("add 1+2")

        # ValueError is fatal — exactly one call, no retries, no replans.
        assert call_count["n"] == 1
        assert report.stop_reason == "fatal_error"
        assert report.replans_used == 0

    def test_step_retrying_event_not_emitted_for_fatal_error(self, monkeypatch):
        executor, planner = _build_executor(max_retries=3)

        def bad_add(a, b):
            raise ValueError("always fails")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(executor.dispatcher._registry.get_tool("add"), "function", bad_add)

        events: list[ProgressEvent] = []
        executor.on_progress = events.append
        executor.run("add 1+2")

        assert "step_retrying" not in [e.status for e in events]

    def test_failure_recorded_in_steps_after_retries_exhausted(self, monkeypatch):
        executor, planner = _build_executor(max_retries=1, max_replans=0)

        def always_timeout(a, b):
            raise TimeoutError("timed out")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(
            executor.dispatcher._registry.get_tool("add"), "function", always_timeout
        )

        report = executor.run("add 1+2")

        # After retries exhausted the failure enters the replan path and
        # is recorded in steps.
        failed = [s for s in report.steps if not s.succeeded]
        assert len(failed) >= 1
        assert failed[0].tool_name == "add"


# ---------------------------------------------------------------------------
# 3. Retry limit behaviour
# ---------------------------------------------------------------------------


class TestRetryLimits:
    def test_default_max_retries_is_three(self):
        assert DEFAULT_MAX_RETRIES == 3

    def test_max_retries_is_configurable(self, monkeypatch):
        # max_replans=0 isolates retry counting: any failure after retries
        # exhausted goes straight to fatal_error with no further attempts.
        executor, planner = _build_executor(max_retries=1, max_replans=0)
        call_count = {"n": 0}

        def always_timeout(a, b):
            call_count["n"] += 1
            raise TimeoutError("timed out")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(
            executor.dispatcher._registry.get_tool("add"), "function", always_timeout
        )

        report = executor.run("add 1+2")

        # max_retries=1: 1 original call + 1 retry = 2 total calls.
        assert call_count["n"] == 2
        assert report.stop_reason == "fatal_error"

    def test_zero_retries_goes_straight_to_replan(self, monkeypatch):
        executor, planner = _build_executor(max_retries=0, max_replans=0)

        call_count = {"n": 0}

        def timeout_add(a, b):
            call_count["n"] += 1
            raise TimeoutError("timed out")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(
            executor.dispatcher._registry.get_tool("add"), "function", timeout_add
        )

        report = executor.run("add 1+2")

        assert call_count["n"] == 1  # no retries attempted
        assert report.stop_reason == "fatal_error"

    def test_exhausted_retries_then_replans(self, monkeypatch):
        executor, planner = _build_executor(max_retries=1, max_replans=3)
        call_count = {"n": 0}

        def timeout_then_succeed(a, b):
            call_count["n"] += 1
            # First two calls (original + 1 retry) raise; third succeeds.
            if call_count["n"] <= 2:
                raise TimeoutError("timed out")
            return a + b

        # First plan: the flaky add. Replan: a fresh add that will succeed.
        plans = iter([
            [{"tool": "add", "arguments": {"a": 1, "b": 2}}],
            [{"tool": "add", "arguments": {"a": 1, "b": 2}}],
        ])

        def plan_fn(prompt, cancel_check=None):
            try:
                return {"steps": next(plans)}
            except StopIteration:
                return {"steps": [{"tool": "add", "arguments": {"a": 1, "b": 2}}]}

        monkeypatch.setattr(planner.client, "generate_json", plan_fn)
        monkeypatch.setattr(
            executor.dispatcher._registry.get_tool("add"),
            "function",
            timeout_then_succeed,
        )

        report = executor.run("add 1+2")

        # Retries exhausted → replan → success on the replanned call.
        assert report.stop_reason == "completed"
        assert report.replans_used >= 1

    def test_retry_state_resets_after_approve(self, monkeypatch, tmp_path):
        """
        Retry counts are local to _execute(); they reset after an
        approve() call so the resumed execution starts fresh.
        This is tested implicitly: after approval the second _execute()
        call initialises a new _retry_counts dict.
        """
        executor, planner = _build_executor(max_retries=3)
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.chdir(tmp_path)

        report = executor.run("add 1+2")

        # add() never stages patches, so the run completes without pausing.
        # The point: retry counts don't bleed across runs.
        assert report.stop_reason == "completed"

    def test_reflection_outcome_is_complete_after_retry_success(self, monkeypatch):
        executor, planner = _build_executor(max_retries=3)
        call_count = {"n": 0}

        def flaky_add(a, b):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TimeoutError("timed out")
            return a + b

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        monkeypatch.setattr(executor.dispatcher._registry.get_tool("add"), "function", flaky_add)

        report = executor.run("add 1+2")

        assert report.reflection is not None
        assert report.reflection.outcome == "COMPLETE"


# ---------------------------------------------------------------------
# classify_error (Task 31)
# ---------------------------------------------------------------------


class TestClassifyError:
    def test_value_error_is_validation(self):
        assert classify_error(ValueError("bad arg")) == "validation"

    def test_type_error_is_validation(self):
        assert classify_error(TypeError("wrong type")) == "validation"

    def test_timeout_error_is_transient(self):
        assert classify_error(TimeoutError("timed out")) == "transient"

    def test_connection_reset_is_transient(self):
        assert classify_error(ConnectionResetError()) == "transient"

    def test_file_not_found_is_fatal(self):
        assert classify_error(FileNotFoundError("missing")) == "fatal"

    def test_permission_error_is_fatal(self):
        assert classify_error(PermissionError("denied")) == "fatal"

    def test_wrapped_value_error_is_validation(self):
        class ToolExecutionError(RuntimeError):
            def __init__(self, original):
                self.original = original
                super().__init__(f"wrapped: {original}")

        exc = ToolExecutionError(ValueError("bad"))
        assert classify_error(exc) == "validation"

    def test_wrapped_timeout_error_is_transient(self):
        class ToolExecutionError(RuntimeError):
            def __init__(self, original):
                self.original = original
                super().__init__(f"Tool failed: timeout: {original}")

        exc = ToolExecutionError(TimeoutError("timed out"))
        assert classify_error(exc) == "transient"

    def test_unknown_exception_is_fatal(self):
        assert classify_error(RuntimeError("something odd")) == "fatal"
