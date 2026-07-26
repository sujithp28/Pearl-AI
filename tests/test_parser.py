import pytest

from src.llm.parser import ToolCall, ToolParser


def test_parse_valid_tool_call():
    parser = ToolParser()

    tool_call = parser.parse('{"tool": "read_file", "arguments": {"path": "a.txt"}}')

    assert tool_call.tool_name == "read_file"
    assert tool_call.kwargs == {"path": "a.txt"}


def test_parse_rejects_non_dict_json():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse("[1, 2, 3]")


def test_parse_plan_returns_ordered_tool_calls():
    parser = ToolParser()

    steps = parser.parse_plan(
        '{"steps": ['
        '{"tool": "read_file", "arguments": {"path": "a.txt"}},'
        '{"tool": "write_file", "arguments": {"path": "b.txt", "content": "hi"}}'
        "]}"
    )

    assert [step.tool_name for step in steps] == ["read_file", "write_file"]
    assert steps[0].kwargs == {"path": "a.txt"}
    assert steps[1].kwargs == {"path": "b.txt", "content": "hi"}


def test_parse_plan_rejects_missing_steps_key():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse_plan('{"tool": "read_file", "arguments": {}}')


def test_parse_plan_rejects_empty_steps():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse_plan('{"steps": []}')


def test_parse_plan_rejects_non_list_steps():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse_plan('{"steps": "read_file"}')


def test_parse_plan_accepts_step_without_arguments():
    # Missing 'arguments' now defaults to {} — small models often omit it
    # for no-arg tools and this should not crash the planning loop.
    parser = ToolParser()
    steps = parser.parse_plan('{"steps": [{"tool": "git_status"}]}')
    assert len(steps) == 1
    assert steps[0].kwargs == {}


def test_parse_plan_rejects_step_missing_tool():
    parser = ToolParser()
    with pytest.raises(ValueError):
        parser.parse_plan('{"steps": [{"arguments": {}}]}')


class TestToolCallRepr:
    def test_repr_shows_tool_name_and_kwargs(self):
        tc = ToolCall("read_file", (), {"path": "src/main.py"})
        assert "read_file" in repr(tc)
        assert "src/main.py" in repr(tc)

    def test_repr_truncates_long_string_values(self):
        long_content = "x" * 500
        tc = ToolCall("write_file", (), {"path": "f.py", "content": long_content})
        r = repr(tc)
        assert len(r) < 200
        assert "…" in r

    def test_repr_no_kwargs_no_trailing_separator(self):
        tc = ToolCall("none", (), {})
        r = repr(tc)
        assert r == "ToolCall(none)"
        assert ", " not in r

    def test_repr_shows_step_id_when_present(self):
        tc = ToolCall("read_file", (), {"path": "f.py"}, step_id="step-1")
        assert "[step-1]" in repr(tc)


class TestToolCallToDict:
    def test_to_dict_is_json_serializable(self):
        import json

        tc = ToolCall("read_file", (), {"path": "src/main.py"})
        assert json.dumps(tc.to_dict()) is not None

    def test_to_dict_contains_tool_name_and_kwargs(self):
        tc = ToolCall("write_file", (), {"path": "f.py", "content": "x"})
        d = tc.to_dict()
        assert d["tool_name"] == "write_file"
        assert d["kwargs"] == {"path": "f.py", "content": "x"}

    def test_to_dict_omits_step_id_when_none(self):
        tc = ToolCall("none", (), {})
        assert "step_id" not in tc.to_dict()

    def test_to_dict_includes_step_id_when_set(self):
        tc = ToolCall("read_file", (), {}, step_id="s1")
        assert tc.to_dict()["step_id"] == "s1"

    def test_to_dict_omits_depends_on_when_empty(self):
        tc = ToolCall("read_file", (), {})
        assert "depends_on" not in tc.to_dict()

    def test_to_dict_includes_depends_on_when_set(self):
        tc = ToolCall("write_file", (), {}, depends_on=["s1", "s2"])
        assert tc.to_dict()["depends_on"] == ["s1", "s2"]


class TestExecutionStepRepr:
    def _make_step(self, tool_name="read_file", error=None):
        from src.agent.executor import ExecutionStep

        return ExecutionStep(
            iteration=1,
            tool_name=tool_name,
            kwargs={},
            error=error,
        )

    def test_repr_shows_tool_name(self):
        s = self._make_step("write_file")
        assert "write_file" in repr(s)

    def test_repr_shows_ok_on_success(self):
        s = self._make_step()
        assert "ok" in repr(s)

    def test_repr_shows_err_on_failure(self):
        s = self._make_step(error="timeout")
        assert "err=" in repr(s)

    def test_repr_shows_iteration(self):
        s = self._make_step()
        assert "iter=1" in repr(s)


class TestExecutionReportProperties:
    def _make_report(self):
        from src.agent.executor import ExecutionReport, ExecutionStep

        steps = [
            ExecutionStep(iteration=1, tool_name="read_file", kwargs={}, error=None),
            ExecutionStep(
                iteration=2, tool_name="write_file", kwargs={}, error="boom"
            ),
            ExecutionStep(iteration=3, tool_name="git_status", kwargs={}, error=None),
        ]
        return ExecutionReport(steps=steps, stop_reason="completed")

    def test_step_count(self):
        r = self._make_report()
        assert r.step_count == 3

    def test_succeeded_steps(self):
        r = self._make_report()
        names = [s.tool_name for s in r.succeeded_steps]
        assert names == ["read_file", "git_status"]

    def test_failed_steps(self):
        r = self._make_report()
        names = [s.tool_name for s in r.failed_steps]
        assert names == ["write_file"]
