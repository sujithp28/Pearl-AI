"""
Tests for the three defects behind "hello" producing a fatal_error:

1. A greeting entered the autonomous loop and the planner invented
   work for it — `read_file("hello.py")`.
2. `topological_sort` raised a bare KeyError on a dependency reference
   the validator had not yet checked, so a malformed replan crashed
   instead of being reported.
3. Every terminal path emitted `task_completed`, so a failed run
   rendered a green "Done" beside its own error card.
"""
from __future__ import annotations

import pytest

from src.agent.conversational import needs_no_tools
from src.agent.dependency_graph import topological_sort
from src.llm.parser import ToolCall

# ---------------------------------------------------------------------------
# 1. Conversational short-circuit
# ---------------------------------------------------------------------------


class TestNeedsNoTools:
    @pytest.mark.parametrize(
        "prompt",
        [
            "hello", "hi", "hiii", "hiiiii", "hey", "yo", "sup", "howdy",
            "Hello!", "  hey  ", "hi.",
            "good morning", "good evening",
            "thanks", "thank you", "thx", "cheers",
            "ok", "okay", "sure", "got it", "sounds good",
            "bye", "goodbye", "see you",
            "ping",
            "how are you", "who are you", "what can you do",
        ],
    )
    def test_conversational_input_needs_no_tools(self, prompt):
        assert needs_no_tools(prompt) is True, f"{prompt!r} should short-circuit"

    @pytest.mark.parametrize(
        "prompt",
        [
            # Real work — a false positive here silently refuses to do it,
            # which is far worse than falling through to normal planning.
            "fix the login bug",
            "add input validation",
            "read hello.py",
            "run the tests",
            "explain this repository",
            "what does the executor do",
            "create a file called notes.txt",
            "why is the build failing",
            "refactor auth",
            "search the web for FastAPI docs",
            "show me src/main.py",
            "delete the build directory",
            # A courtesy word carrying a request behind it.
            "hi, delete the build directory",
            "hey can you fix the tests",
            "thanks, now add a docstring",
            # Code-ish content.
            "def add(a, b):",
            "src/agent/planner.py",
            # Ambiguous words that plausibly mean real work. Refusing
            # these silently would be worse than planning for them.
            "test",
            "testing",
        ],
    )
    def test_real_requests_are_not_short_circuited(self, prompt):
        assert needs_no_tools(prompt) is False, (
            f"{prompt!r} is real work and must reach the planner"
        )

    @pytest.mark.parametrize("prompt", ["", "   ", "\n", None])
    def test_empty_input_is_not_conversational(self, prompt):
        # Empty input is not a greeting; it is nothing. Let the normal
        # path reject it rather than reporting a completed task.
        assert needs_no_tools(prompt or "") is False

    def test_long_input_is_never_short_circuited(self):
        """Length alone is evidence of substance."""
        assert needs_no_tools("hi " * 20) is False


class TestUnactionableInput:
    """
    Reported: typing "lpoe" made Pearl search for it, fail to read
    lpoe.py, search again, then CREATE lpoe.py — and reflection called
    that "complete, confidence 100%".

    Every step behaved correctly in isolation. The run should never have
    started. Guessing at meaningless input is the failure mode worth
    preventing precisely because its output looks like success.
    """

    @pytest.mark.parametrize(
        "prompt", ["lpoe", "jiii", "asdf", "qwer", "zz", "blah", ""]
    )
    def test_unactionable_input_needs_clarification(self, prompt):
        from src.agent.conversational import needs_clarification

        assert needs_clarification(prompt) is True

    @pytest.mark.parametrize(
        "prompt",
        [
            # A verb is intent, even with few words.
            "run tests", "fix login", "add docstring", "explain executor",
            # A path or extension is intent.
            "src/main.py", "README.md", "read config.yaml",
            # Code is intent.
            "def add(a, b):",
            # Enough words carry intent without a recognised verb.
            "the login page is broken",
            # Two words is already past the gate — anything longer than a
            # bare token plausibly carries intent this check cannot see,
            # and refusing real work is the costly direction.
            "xyz abc",
            "say hello",
        ],
    )
    def test_real_requests_do_not_need_clarification(self, prompt):
        from src.agent.conversational import needs_clarification

        assert needs_clarification(prompt) is False

    def test_a_bare_tool_name_is_actionable(self):
        """
        An early version guessed at meaninglessness and rejected one-word
        commands — including tool names, which are the most actionable
        input there is. Naming a tool Pearl has is a request to use it.
        """
        from src.agent.conversational import needs_clarification

        assert needs_clarification("boom", known_terms={"boom", "add"}) is False
        assert needs_clarification("lpoe", known_terms={"boom", "add"}) is True

    def test_planner_treats_its_own_tools_as_actionable(self):
        from src.agent.conversational import AmbiguousRequestError
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.planner import Planner
        from src.tools.metadata import tool
        from src.tools.registry import ToolRegistry

        @tool(description="Test tool.", returns="None")
        def zqx() -> None:
            return None

        registry = ToolRegistry()
        registry.register(zqx)
        planner = Planner(registry, ToolDispatcher(registry))

        # Would look like gibberish, but it names a registered tool.
        try:
            planner.plan("zqx")
        except AmbiguousRequestError:
            pytest.fail("a registered tool name was treated as unactionable")
        except Exception:
            pass  # any other planning outcome is fine; only the gate matters

    @pytest.mark.parametrize("prompt", ["hello", "hi", "namaste", "thanks"])
    def test_greetings_are_answered_not_questioned(self, prompt):
        """
        A greeting is unactionable too, but asking "what did you mean by
        hello?" is worse than answering it.
        """
        from src.agent.conversational import needs_clarification

        assert needs_clarification(prompt) is False

    def test_planner_refuses_rather_than_guessing(self):
        from src.agent.conversational import AmbiguousRequestError
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.planner import Planner
        from src.tools.registry import ToolRegistry

        registry = ToolRegistry()
        planner = Planner(registry, ToolDispatcher(registry))

        with pytest.raises(AmbiguousRequestError):
            planner.plan("lpoe")

    def test_refusal_does_not_call_the_model(self, monkeypatch):
        """Asking the model again only raises the odds it invents work."""
        from src.agent.conversational import AmbiguousRequestError
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.planner import Planner
        from src.tools.registry import ToolRegistry

        registry = ToolRegistry()
        planner = Planner(registry, ToolDispatcher(registry))
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            lambda *a, **k: pytest.fail("model was called for unactionable input"),
        )

        with pytest.raises(AmbiguousRequestError):
            planner.plan("lpoe")

    def test_run_creates_nothing_and_asks_instead(self, tmp_path):
        """The end-to-end guarantee: noise in, no filesystem change."""
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.executor import AutonomousExecutor
        from src.agent.planner import Planner
        from src.config.workspace import clear_workspace_root, set_workspace_root
        from src.main import build_registry

        set_workspace_root(tmp_path)
        try:
            registry = build_registry()
            dispatcher = ToolDispatcher(registry)
            executor = AutonomousExecutor(
                Planner(registry, dispatcher), dispatcher, checkpoints=None
            )

            report = executor.run("lpoe")

            assert report.steps == [], "no step should have run"
            assert list(tmp_path.iterdir()) == [], "created a file from noise"
            assert not executor.patch_manager.has_pending(), "staged a change"
            assert report.error and "not sure what you'd like" in report.error
        finally:
            clear_workspace_root()

    def test_ambiguous_request_is_not_retried(self, tmp_path):
        """
        The retry path exists for a model that produced a bad plan. Here
        the model was never asked, and asking now would invite exactly
        the invention this prevents — so the refusal must propagate on
        the first attempt rather than triggering a second.
        """
        from src.agent.conversational import AmbiguousRequestError
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.executor import AutonomousExecutor
        from src.agent.planner import Planner
        from src.config.workspace import clear_workspace_root, set_workspace_root
        from src.tools.registry import ToolRegistry

        set_workspace_root(tmp_path)
        try:
            registry = ToolRegistry()
            planner = Planner(registry, ToolDispatcher(registry))
            calls = {"replan": 0}
            planner.replan = lambda *a, **k: calls.__setitem__(  # type: ignore[method-assign]
                "replan", calls["replan"] + 1
            )

            executor = AutonomousExecutor(
                planner, ToolDispatcher(registry), checkpoints=None
            )

            with pytest.raises(AmbiguousRequestError):
                executor._plan_with_retry("lpoe", "", [], [])

            assert calls["replan"] == 0, "refusal triggered a retry"
        finally:
            clear_workspace_root()


class TestPlannerShortCircuit:
    def _planner(self):
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.planner import Planner
        from src.tools.registry import ToolRegistry

        registry = ToolRegistry()
        return Planner(registry, ToolDispatcher(registry))

    def test_greeting_plans_the_none_sentinel(self):
        steps = list(self._planner().plan("hello"))
        assert len(steps) == 1
        assert steps[0].tool_name == "none"

    def test_greeting_does_not_call_the_model(self, monkeypatch):
        """
        The whole point of a deterministic check: no latency, and no
        chance of judging identical input differently on two runs.
        """
        planner = self._planner()
        called = {"n": 0}

        def _boom(*args, **kwargs):
            called["n"] += 1
            raise AssertionError("planner called the model for a greeting")

        monkeypatch.setattr(planner.client, "generate_json", _boom)

        steps = list(planner.plan("hi"))

        assert called["n"] == 0
        assert steps[0].tool_name == "none"

    def test_confidence_is_recorded_for_the_short_circuit(self):
        """Callers read last_confidence_score after every plan()."""
        planner = self._planner()
        planner.plan("thanks")
        assert planner.last_confidence_score is not None


# ---------------------------------------------------------------------------
# 2. topological_sort must not crash on an unvalidated reference
# ---------------------------------------------------------------------------


def _step(tool: str, step_id: str | None = None, depends_on=None) -> ToolCall:
    call = ToolCall(tool_name=tool, args=(), kwargs={})
    call.step_id = step_id
    call.depends_on = depends_on or []
    return call


class TestTopologicalSortUnknownDeps:
    def test_unknown_dependency_does_not_raise_keyerror(self):
        """
        Both plan() and replan() sort *before* validating — deliberately,
        so ordering rules see execution order. A model naming a tool
        instead of a step id used to crash the run with a bare KeyError.
        """
        steps = [
            _step("write_file", step_id="a", depends_on=["read_file"]),
        ]

        result = topological_sort(steps)  # must not raise

        assert len(result) == 1

    def test_unknown_dependency_still_returns_every_step(self):
        steps = [
            _step("read_file", step_id="s1"),
            _step("write_file", step_id="s2", depends_on=["nonexistent"]),
        ]

        result = topological_sort(steps)

        assert {s.tool_name for s in result} == {"read_file", "write_file"}

    def test_known_dependencies_are_still_ordered(self):
        """Dropping unknown edges must not weaken real ordering."""
        steps = [
            _step("write_file", step_id="w", depends_on=["r"]),
            _step("read_file", step_id="r"),
        ]

        result = topological_sort(steps)

        assert [s.tool_name for s in result] == ["read_file", "write_file"]

    def test_mixed_known_and_unknown_deps_orders_by_the_known_one(self):
        steps = [
            _step("write_file", step_id="w", depends_on=["r", "ghost"]),
            _step("read_file", step_id="r"),
        ]

        result = topological_sort(steps)

        assert [s.tool_name for s in result] == ["read_file", "write_file"]

    def test_validator_reports_the_unknown_reference(self):
        """
        Skipping the edge must not hide the problem — the validator is
        what should surface it, with a usable message.
        """
        from src.agent.plan_validator import PlanValidationError, validate_dependencies

        steps = [_step("write_file", step_id="a", depends_on=["read_file"])]

        with pytest.raises(PlanValidationError) as exc:
            validate_dependencies(steps)

        assert "read_file" in str(exc.value)


# ---------------------------------------------------------------------------
# 3. Failure must not report itself as completion
# ---------------------------------------------------------------------------


class TestFailureStatus:
    def test_task_failed_is_a_valid_progress_status(self):
        from typing import get_args

        from src.agent.executor import ProgressStatus

        assert "task_failed" in get_args(ProgressStatus)

    def test_failure_paths_emit_task_failed_not_task_completed(self):
        """
        A failed run used to emit `task_completed` with only the
        personality wording differing, so the UI — which keys off status —
        drew a green "Done" next to the error.
        """
        import inspect

        from src.agent import executor as ex

        source = inspect.getsource(ex)

        # Every FAILURE-flavoured emit must now carry task_failed.
        failure_blocks = source.count(
            'current_action=self._personality.format(EventKind.FAILURE)'
        )
        assert failure_blocks > 0, "no failure emits found — test is stale"

        for block in source.split("self._emit(")[1:]:
            head = block[:400]
            if "EventKind.FAILURE" in head:
                assert '"task_failed"' in head, (
                    "a failure emit still reports task_completed:\n"
                    f"{head[:200]}"
                )

    def test_missing_llama_cpp_names_the_interpreter(self):
        """
        The usual cause is the wrong Python, not a missing package: a bare
        `uvicorn` runs under whichever uvicorn is first on PATH, often an
        unrelated virtualenv. Telling the user to install a package they
        already have — into the environment that already has it — sends
        them the wrong way, so the message must name the interpreter.
        """
        import sys

        import src.llm.providers.local_inference as mod

        saved_module = sys.modules.get("llama_cpp")
        saved_cache = dict(mod._llms)
        sys.modules["llama_cpp"] = None
        mod._llms.clear()
        try:
            with pytest.raises(ImportError) as exc:
                mod._get_shared_llm("fake.gguf", 512, 1)

            message = str(exc.value)
            assert sys.executable in message, "must name the interpreter"
            assert "python -m uvicorn" in message, "must give the correct command"
            assert "wrong interpreter" in message
        finally:
            if saved_module is None:
                sys.modules.pop("llama_cpp", None)
            else:
                sys.modules["llama_cpp"] = saved_module
            mod._llms.update(saved_cache)

    def test_planning_failure_explains_itself(self, monkeypatch, tmp_path):
        """
        A planning failure produces no steps, so a client reporting the
        failed step has nothing to show and falls back to the bare
        stop_reason. "fatal_error" tells the user nothing about what
        happened or what to try instead.
        """
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.executor import AutonomousExecutor
        from src.agent.planner import Planner
        from src.config.workspace import clear_workspace_root, set_workspace_root
        from src.tools.registry import ToolRegistry

        set_workspace_root(tmp_path)
        try:
            registry = ToolRegistry()
            planner = Planner(registry, ToolDispatcher(registry))
            monkeypatch.setattr(
                planner.client,
                "generate_json",
                lambda *a, **k: (_ for _ in ()).throw(
                    ValueError("Expected JSON object, got prose")
                ),
            )

            executor = AutonomousExecutor(
                planner, ToolDispatcher(registry), checkpoints=None
            )
            report = executor.run("jiii")

            assert report.steps == [], "planning failed, so there are no steps"
            assert report.error, "a failure with no steps must explain itself"
            # Actionable, not a raw exception echoed back.
            assert "fatal_error" not in report.error
            assert "Chat mode" in report.error or "rephras" in report.error.lower()
        finally:
            clear_workspace_root()

    def test_error_reaches_the_api_payload(self, monkeypatch, tmp_path):
        """The UI can only show what the payload carries."""
        from src.agent.executor import ExecutionReport
        from src.api.session import _report_to_dict

        report = ExecutionReport(
            steps=[], stop_reason="fatal_error", error="Could not plan that."
        )

        payload = _report_to_dict(report)

        assert payload["error"] == "Could not plan that."

    def test_successful_report_carries_no_error_key(self):
        from src.agent.executor import ExecutionReport
        from src.api.session import _report_to_dict

        payload = _report_to_dict(ExecutionReport(stop_reason="completed"))

        assert "error" not in payload

    def test_planning_failure_reports_failure_status(self, monkeypatch, tmp_path):
        """End-to-end: a run that cannot plan must not look successful."""
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.executor import AutonomousExecutor
        from src.agent.planner import Planner
        from src.config.workspace import clear_workspace_root, set_workspace_root
        from src.tools.registry import ToolRegistry

        set_workspace_root(tmp_path)
        try:
            registry = ToolRegistry()
            planner = Planner(registry, ToolDispatcher(registry))
            monkeypatch.setattr(
                planner.client,
                "generate_json",
                lambda *a, **k: (_ for _ in ()).throw(ValueError("bad json")),
            )

            executor = AutonomousExecutor(planner, ToolDispatcher(registry), checkpoints=None)
            report = executor.run("do something that cannot be planned")

            assert report.stop_reason == "fatal_error"
            statuses = [e.status for e in report.events]
            assert "task_failed" in statuses
            assert "task_completed" not in statuses, (
                "a failed run reported task_completed"
            )
        finally:
            clear_workspace_root()
