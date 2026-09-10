"""
Tests for dependency_graph.topological_sort() and
plan_validator.validate_dependencies().

Unit tests target the pure functions directly. Integration tests run
the full Planner path so that the parser → validator → sort chain is
exercised end-to-end without a real LLM.

Coverage targets
----------------
- Fast path: no annotations → original order preserved
- Linear chain A → B → C
- Diamond: A → B, A → C, B → D, C → D
- Independent groups: {A,B} → C, {D,E} → F
- Single-node plan (annotated and un-annotated)
- Cycle detection (direct, transitive, self-dep via validator)
- Duplicate step IDs (validator)
- Self-dependency (validator)
- Missing dependency reference (validator)
- Partial annotation: some steps have ids, some don't
- Backward-compatibility: plain ToolCall(tool_name, args, kwargs)
  construction still works (no positional-argument breakage)
- Planner integration: plan() applies sort; replan() applies sort
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agent.dependency_graph import DependencyCycleError, topological_sort
from src.agent.dispatcher import ToolDispatcher
from src.agent.plan_validator import (
    PlanValidationError,
    validate_dependencies,
    validate_plan,
)
from src.agent.planner import Planner
from src.llm.client import LLMClient
from src.llm.parser import ParseError, ToolCall, ToolParser
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tc(
    tool_name: str,
    *,
    step_id: str | None = None,
    depends_on: list[str] | None = None,
) -> ToolCall:
    """Minimal ToolCall factory for tests."""
    return ToolCall(
        tool_name=tool_name,
        args=(),
        kwargs={},
        step_id=step_id,
        depends_on=depends_on or [],
    )


def _names(steps: list[ToolCall]) -> list[str]:
    """Extract tool_name sequence for readable assertions."""
    return [s.tool_name for s in steps]


# ---------------------------------------------------------------------------
# Backward-compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Ensure the ToolCall schema extension is non-breaking."""

    def test_positional_construction_still_works(self) -> None:
        tc = ToolCall("read_file", (), {"path": "a.txt"})
        assert tc.tool_name == "read_file"
        assert tc.step_id is None
        assert tc.depends_on == []

    def test_keyword_construction_without_new_fields(self) -> None:
        tc = ToolCall(tool_name="write_file", args=(), kwargs={"path": "b.txt"})
        assert tc.step_id is None
        assert tc.depends_on == []

    def test_new_fields_accept_correct_types(self) -> None:
        tc = ToolCall(
            tool_name="read_file",
            args=(),
            kwargs={},
            step_id="step-1",
            depends_on=["step-0"],
        )
        assert tc.step_id == "step-1"
        assert tc.depends_on == ["step-0"]

    def test_depends_on_default_is_independent_per_instance(self) -> None:
        a = ToolCall("a", (), {})
        b = ToolCall("b", (), {})
        a.depends_on.append("x")
        assert b.depends_on == [], "mutable default must not be shared"


# ---------------------------------------------------------------------------
# Parser: optional fields round-trip
# ---------------------------------------------------------------------------


class TestParserOptionalFields:
    """ToolParser correctly extracts id and depends_on from plan JSON."""

    def setup_method(self) -> None:
        self.parser = ToolParser()

    def _plan(self, steps: list[dict[str, Any]]) -> str:
        return json.dumps({"steps": steps})

    def test_no_annotations_parses_cleanly(self) -> None:
        plan = self._plan([{"tool": "read_file", "arguments": {"path": "a.txt"}}])
        steps = self.parser.parse_plan(plan)
        assert steps[0].step_id is None
        assert steps[0].depends_on == []

    def test_id_field_is_extracted(self) -> None:
        plan = self._plan([
            {"tool": "read_file", "id": "r1", "arguments": {"path": "a.txt"}}
        ])
        steps = self.parser.parse_plan(plan)
        assert steps[0].step_id == "r1"

    def test_depends_on_field_is_extracted(self) -> None:
        plan = self._plan([
            {"tool": "read_file",  "id": "r1", "arguments": {}},
            {"tool": "write_file", "id": "w1", "depends_on": ["r1"], "arguments": {}},
        ])
        steps = self.parser.parse_plan(plan)
        assert steps[0].depends_on == []
        assert steps[1].depends_on == ["r1"]

    def test_non_string_id_raises_parse_error(self) -> None:
        plan = self._plan([{"tool": "read_file", "id": 42, "arguments": {}}])
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse_plan(plan)
        assert exc_info.value.reason == "invalid_type"
        assert exc_info.value.field == "id"

    def test_non_list_depends_on_raises_parse_error(self) -> None:
        plan = self._plan([
            {"tool": "read_file", "id": "r1", "depends_on": "r0", "arguments": {}}
        ])
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse_plan(plan)
        assert exc_info.value.reason == "invalid_type"
        assert exc_info.value.field == "depends_on"

    def test_non_string_entry_in_depends_on_raises_parse_error(self) -> None:
        plan = self._plan([
            {"tool": "read_file", "id": "r1", "depends_on": [99], "arguments": {}}
        ])
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse_plan(plan)
        assert exc_info.value.reason == "invalid_type"
        assert exc_info.value.field == "depends_on"


# ---------------------------------------------------------------------------
# topological_sort — fast path
# ---------------------------------------------------------------------------


class TestTopologicalSortFastPath:
    """When no step has annotations, original order is preserved exactly."""

    def test_empty_list_returns_empty(self) -> None:
        assert topological_sort([]) == []

    def test_single_unannotated_step(self) -> None:
        steps = [_tc("a")]
        result = topological_sort(steps)
        assert result is not steps  # returns a copy
        assert _names(result) == ["a"]

    def test_multiple_unannotated_steps_preserve_order(self) -> None:
        steps = [_tc("a"), _tc("b"), _tc("c")]
        assert _names(topological_sort(steps)) == ["a", "b", "c"]

    def test_returns_copy_not_same_list(self) -> None:
        steps = [_tc("a"), _tc("b")]
        result = topological_sort(steps)
        assert result is not steps


# ---------------------------------------------------------------------------
# topological_sort — linear chains
# ---------------------------------------------------------------------------


class TestTopologicalSortLinearChain:
    """A → B → C must produce [A, B, C]."""

    def test_two_step_chain_in_order(self) -> None:
        steps = [
            _tc("a", step_id="a"),
            _tc("b", step_id="b", depends_on=["a"]),
        ]
        assert _names(topological_sort(steps)) == ["a", "b"]

    def test_two_step_chain_reversed_in_plan(self) -> None:
        # Plan declares B first, but B depends on A — A must run first.
        steps = [
            _tc("b", step_id="b", depends_on=["a"]),
            _tc("a", step_id="a"),
        ]
        assert _names(topological_sort(steps)) == ["a", "b"]

    def test_three_step_chain(self) -> None:
        steps = [
            _tc("a", step_id="a"),
            _tc("b", step_id="b", depends_on=["a"]),
            _tc("c", step_id="c", depends_on=["b"]),
        ]
        assert _names(topological_sort(steps)) == ["a", "b", "c"]

    def test_three_step_chain_reverse_declared_order(self) -> None:
        steps = [
            _tc("c", step_id="c", depends_on=["b"]),
            _tc("b", step_id="b", depends_on=["a"]),
            _tc("a", step_id="a"),
        ]
        assert _names(topological_sort(steps)) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# topological_sort — diamond
# ---------------------------------------------------------------------------


class TestTopologicalSortDiamond:
    """
    A → B, A → C, B → D, C → D

    D must come last. B and C may appear in either order relative to
    each other (both become ready simultaneously after A). We only
    assert the invariant: A first, D last, B and C in between.
    """

    def _diamond_steps(self) -> list[ToolCall]:
        return [
            _tc("a", step_id="a"),
            _tc("b", step_id="b", depends_on=["a"]),
            _tc("c", step_id="c", depends_on=["a"]),
            _tc("d", step_id="d", depends_on=["b", "c"]),
        ]

    def test_a_is_first(self) -> None:
        result = topological_sort(self._diamond_steps())
        assert result[0].tool_name == "a"

    def test_d_is_last(self) -> None:
        result = topological_sort(self._diamond_steps())
        assert result[-1].tool_name == "d"

    def test_b_and_c_are_in_between(self) -> None:
        result = _names(topological_sort(self._diamond_steps()))
        assert set(result[1:3]) == {"b", "c"}

    def test_all_four_steps_present(self) -> None:
        result = topological_sort(self._diamond_steps())
        assert len(result) == 4

    def test_original_order_breaks_ties(self) -> None:
        # b appears before c in the original list, so b must be processed
        # first when both become ready simultaneously.
        result = _names(topological_sort(self._diamond_steps()))
        b_pos = result.index("b")
        c_pos = result.index("c")
        assert b_pos < c_pos, "original order must break ties deterministically"


# ---------------------------------------------------------------------------
# topological_sort — independent groups
# ---------------------------------------------------------------------------


class TestTopologicalSortIndependentGroups:
    """
    Two independent fan-in groups: {a,b}→c and {d,e}→f
    """

    def _group_steps(self) -> list[ToolCall]:
        return [
            _tc("a", step_id="a"),
            _tc("b", step_id="b"),
            _tc("c", step_id="c", depends_on=["a", "b"]),
            _tc("d", step_id="d"),
            _tc("e", step_id="e"),
            _tc("f", step_id="f", depends_on=["d", "e"]),
        ]

    def test_c_comes_after_a_and_b(self) -> None:
        result = _names(topological_sort(self._group_steps()))
        c_pos = result.index("c")
        assert result.index("a") < c_pos
        assert result.index("b") < c_pos

    def test_f_comes_after_d_and_e(self) -> None:
        result = _names(topological_sort(self._group_steps()))
        f_pos = result.index("f")
        assert result.index("d") < f_pos
        assert result.index("e") < f_pos

    def test_all_six_steps_present(self) -> None:
        assert len(topological_sort(self._group_steps())) == 6


# ---------------------------------------------------------------------------
# topological_sort — partial annotation
# ---------------------------------------------------------------------------


class TestTopologicalSortPartialAnnotation:
    """Steps without IDs are treated as anonymous independent nodes."""

    def test_unannotated_steps_interleave_preserving_relative_order(self) -> None:
        # Step "anon1" has no id and no deps — it's independent.
        # Step "named" depends on nothing; step "after_named" depends on "named".
        steps = [
            _tc("anon1"),  # no id, no deps
            _tc("named",       step_id="named"),
            _tc("after_named", step_id="after_named", depends_on=["named"]),
            _tc("anon2"),  # no id, no deps
        ]
        result = topological_sort(steps)
        names = _names(result)
        # after_named must come after named
        assert names.index("after_named") > names.index("named")
        # all four present
        assert len(result) == 4

    def test_unannotated_step_alone_survives_sort(self) -> None:
        steps = [_tc("anon")]
        result = topological_sort(steps)
        assert _names(result) == ["anon"]


# ---------------------------------------------------------------------------
# topological_sort — cycle detection
# ---------------------------------------------------------------------------


class TestTopologicalSortCycleDetection:
    """Kahn's algorithm raises DependencyCycleError on a cycle."""

    def test_two_node_cycle(self) -> None:
        steps = [
            _tc("a", step_id="a", depends_on=["b"]),
            _tc("b", step_id="b", depends_on=["a"]),
        ]
        with pytest.raises(DependencyCycleError):
            topological_sort(steps)

    def test_three_node_cycle(self) -> None:
        steps = [
            _tc("a", step_id="a", depends_on=["c"]),
            _tc("b", step_id="b", depends_on=["a"]),
            _tc("c", step_id="c", depends_on=["b"]),
        ]
        with pytest.raises(DependencyCycleError):
            topological_sort(steps)

    def test_cycle_in_larger_graph(self) -> None:
        # a → b → c → d, but also d → b (cycle b→c→d→b)
        steps = [
            _tc("a", step_id="a"),
            _tc("b", step_id="b", depends_on=["a", "d"]),
            _tc("c", step_id="c", depends_on=["b"]),
            _tc("d", step_id="d", depends_on=["c"]),
        ]
        with pytest.raises(DependencyCycleError):
            topological_sort(steps)

    def test_error_message_is_informative(self) -> None:
        steps = [
            _tc("a", step_id="a", depends_on=["b"]),
            _tc("b", step_id="b", depends_on=["a"]),
        ]
        with pytest.raises(DependencyCycleError, match="cycle"):
            topological_sort(steps)


# ---------------------------------------------------------------------------
# validate_dependencies — structural errors
# ---------------------------------------------------------------------------


class TestValidateDependenciesDuplicateIds:
    def test_duplicate_step_id_raises(self) -> None:
        steps = [
            _tc("a", step_id="dup"),
            _tc("b", step_id="dup"),
        ]
        with pytest.raises(PlanValidationError, match="duplicate step_id"):
            validate_dependencies(steps)

    def test_duplicate_step_id_error_names_first_occurrence(self) -> None:
        steps = [
            _tc("a", step_id="dup"),
            _tc("b", step_id="dup"),
        ]
        with pytest.raises(PlanValidationError, match="step 1"):
            validate_dependencies(steps)

    def test_unique_step_ids_pass(self) -> None:
        steps = [
            _tc("a", step_id="s1"),
            _tc("b", step_id="s2"),
        ]
        validate_dependencies(steps)  # must not raise


class TestValidateDependenciesSelfDep:
    def test_self_dependency_raises(self) -> None:
        steps = [_tc("a", step_id="a", depends_on=["a"])]
        with pytest.raises(PlanValidationError, match="depends on itself"):
            validate_dependencies(steps)


class TestValidateDependenciesMissingRef:
    def test_unknown_dep_id_raises(self) -> None:
        steps = [
            _tc("a", step_id="a", depends_on=["nonexistent"]),
        ]
        with pytest.raises(PlanValidationError, match="nonexistent"):
            validate_dependencies(steps)

    def test_multiple_errors_reported_together(self) -> None:
        # Two separate problems: unknown ref and duplicate id.
        steps = [
            _tc("a", step_id="dup"),
            _tc("b", step_id="dup"),
            _tc("c", step_id="c", depends_on=["ghost"]),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            validate_dependencies(steps)
        msg = str(exc_info.value)
        assert "duplicate" in msg
        assert "ghost" in msg


class TestValidateDependenciesCycles:
    def test_cycle_raises_plan_validation_error(self) -> None:
        steps = [
            _tc("a", step_id="a", depends_on=["b"]),
            _tc("b", step_id="b", depends_on=["a"]),
        ]
        with pytest.raises(PlanValidationError, match="cycle"):
            validate_dependencies(steps)

    def test_no_cycle_passes(self) -> None:
        steps = [
            _tc("a", step_id="a"),
            _tc("b", step_id="b", depends_on=["a"]),
        ]
        validate_dependencies(steps)  # must not raise


# ---------------------------------------------------------------------------
# validate_plan integration — dependencies wired in
# ---------------------------------------------------------------------------


class TestValidatePlanWithDependencies:
    """validate_plan() calls validate_dependencies() when annotations present."""

    def test_valid_annotated_plan_passes(self) -> None:
        steps = [
            _tc("read_file", step_id="r1"),
            _tc("write_file", step_id="w1", depends_on=["r1"]),
        ]
        validate_plan(steps)  # must not raise

    def test_cycle_in_annotated_plan_raises(self) -> None:
        steps = [
            _tc("a", step_id="a", depends_on=["b"]),
            _tc("b", step_id="b", depends_on=["a"]),
        ]
        with pytest.raises(PlanValidationError):
            validate_plan(steps)

    def test_unannotated_plan_skips_dependency_check(self) -> None:
        # Plain steps with no ids — validate_plan must not call
        # validate_dependencies and must pass cleanly.
        steps = [_tc("a"), _tc("b"), _tc("c")]
        validate_plan(steps)  # must not raise

    def test_mixed_annotation_partial_skips_correctly(self) -> None:
        # Only one step has an id — still triggers validate_dependencies.
        steps = [_tc("a", step_id="a"), _tc("b")]
        validate_plan(steps)  # must not raise

    def test_duplicate_id_caught_through_validate_plan(self) -> None:
        steps = [
            _tc("a", step_id="x"),
            _tc("b", step_id="x"),
        ]
        with pytest.raises(PlanValidationError, match="duplicate"):
            validate_plan(steps)


# ---------------------------------------------------------------------------
# Planner integration
# ---------------------------------------------------------------------------


# Stub tools registered once at module level for the planner integration tests.
# The tool names must match exactly what the scripted LLM returns.

@tool(description="Read a file.", parameters={"path": "str"}, returns="str")
def read_file(path: str) -> str:
    return ""


@tool(description="Write a file.", parameters={"path": "str", "content": "str"}, returns="str")
def write_file(path: str = "", content: str = "") -> str:
    return ""


@tool(description="Search files.", parameters={"pattern": "str"}, returns="str")
def search_files(pattern: str = "") -> str:
    return ""


def _scripted_planner(plan_json: dict[str, Any]) -> Planner:
    """Build a Planner whose LLM always returns plan_json."""
    registry = ToolRegistry()
    registry.register(read_file)
    registry.register(write_file)
    registry.register(search_files)
    dispatcher = MagicMock(spec=ToolDispatcher)
    client = MagicMock(spec=LLMClient)
    client.generate_json.return_value = plan_json
    client.load_prompt.return_value = "{tools}{user_prompt}{workspace_root}"
    return Planner(registry=registry, dispatcher=dispatcher, client=client)


class TestPlannerAppliesTopologicalSort:
    """plan() and replan() produce steps in dependency order."""

    def test_plan_reorders_by_dependency(self) -> None:
        # LLM returns write_file before read_file, but write depends on read.
        plan_json = {
            "steps": [
                {
                    "tool": "write_file",
                    "id": "w",
                    "depends_on": ["r"],
                    "arguments": {"path": "b.txt", "content": "x"},
                },
                {
                    "tool": "read_file",
                    "id": "r",
                    "arguments": {"path": "a.txt"},
                },
            ]
        }
        planner = _scripted_planner(plan_json)
        steps = planner.plan("test")
        assert _names(steps) == ["read_file", "write_file"]

    def test_plan_preserves_order_when_no_deps(self) -> None:
        plan_json = {
            "steps": [
                {"tool": "read_file",  "arguments": {"path": "a.txt"}},
                {"tool": "search_files", "arguments": {"pattern": "*.py"}},
            ]
        }
        planner = _scripted_planner(plan_json)
        steps = planner.plan("test")
        assert _names(steps) == ["read_file", "search_files"]

    def test_plan_raises_on_cycle(self) -> None:
        plan_json = {
            "steps": [
                {"tool": "read_file",  "id": "a", "depends_on": ["b"], "arguments": {}},
                {"tool": "write_file", "id": "b", "depends_on": ["a"], "arguments": {}},
            ]
        }
        planner = _scripted_planner(plan_json)
        with pytest.raises((PlanValidationError, ValueError)):
            planner.plan("test")

    def test_replan_also_applies_topological_sort(self) -> None:
        plan_json = {
            "steps": [
                {
                    "tool": "write_file",
                    "id": "w",
                    "depends_on": ["r"],
                    "arguments": {"path": "out.txt", "content": "done"},
                },
                {
                    "tool": "read_file",
                    "id": "r",
                    "arguments": {"path": "src.txt"},
                },
            ]
        }
        planner = _scripted_planner(plan_json)
        steps = planner.replan("test", completed=[], failed={"tool": "x", "error": "e"})
        assert _names(steps) == ["read_file", "write_file"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestTopologicalSortEdgeCases:
    def test_single_annotated_step_no_deps(self) -> None:
        steps = [_tc("a", step_id="a")]
        assert _names(topological_sort(steps)) == ["a"]

    def test_fan_out(self) -> None:
        # a → b, a → c, a → d
        steps = [
            _tc("a", step_id="a"),
            _tc("b", step_id="b", depends_on=["a"]),
            _tc("c", step_id="c", depends_on=["a"]),
            _tc("d", step_id="d", depends_on=["a"]),
        ]
        result = topological_sort(steps)
        assert result[0].tool_name == "a"
        assert set(_names(result[1:])) == {"b", "c", "d"}

    def test_fan_in(self) -> None:
        # a, b, c → d
        steps = [
            _tc("a", step_id="a"),
            _tc("b", step_id="b"),
            _tc("c", step_id="c"),
            _tc("d", step_id="d", depends_on=["a", "b", "c"]),
        ]
        result = topological_sort(steps)
        assert result[-1].tool_name == "d"
        assert len(result) == 4

    def test_result_contains_same_objects(self) -> None:
        # topological_sort must return the same ToolCall objects,
        # not copies, so callers see identical step_id / depends_on values.
        steps = [
            _tc("a", step_id="a"),
            _tc("b", step_id="b", depends_on=["a"]),
        ]
        result = topological_sort(steps)
        assert result[0] is steps[0]
        assert result[1] is steps[1]

    def test_many_independent_steps_preserve_order(self) -> None:
        # 10 steps, all independent, all annotated with ids — original
        # order must be preserved because none depend on any other.
        steps = [_tc(f"t{i}", step_id=f"s{i}") for i in range(10)]
        result = topological_sort(steps)
        assert _names(result) == [f"t{i}" for i in range(10)]
