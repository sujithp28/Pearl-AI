"""
Tests for the planning fine-tune dataset generator.

The dataset's whole value is that its labels are correct by
construction rather than by review, so the properties worth testing are
the ones that would silently poison a training run: a prompt that isn't
the one Pearl actually uses, a completion the parser would reject, or a
label that teaches the model the behaviour being fixed.
"""

from __future__ import annotations

import json

import pytest

from finetune.build_dataset import collect_examples, render_prompt


@pytest.fixture(scope="module")
def examples():
    return collect_examples()


class TestExamples:
    def test_dataset_is_not_empty(self, examples):
        assert len(examples) > 50

    def test_no_duplicate_requests(self, examples):
        """
        A duplicated request is trained twice, weighting it arbitrarily.
        """
        requests = [text.lower() for text, _ in examples]
        assert len(requests) == len(set(requests))

    def test_every_completion_is_valid_json(self, examples):
        for text, plan in examples:
            encoded = json.dumps(plan)
            assert json.loads(encoded) == plan, f"bad plan for {text!r}"

    def test_every_plan_has_a_steps_array(self, examples):
        for text, plan in examples:
            assert isinstance(plan.get("steps"), list), f"no steps for {text!r}"
            assert plan["steps"], f"empty steps for {text!r}"

    def test_every_step_names_a_tool(self, examples):
        for text, plan in examples:
            for step in plan["steps"]:
                assert "tool" in step, f"step without a tool in {text!r}"
                assert "arguments" in step, f"step without arguments in {text!r}"


class TestLabelsMatchPearlsRules:
    """
    The labels must teach the behaviour the prompt already asks for and
    the model ignores — not merely be well-formed.
    """

    def _plan_for(self, examples, request):
        for text, plan in examples:
            if text == request:
                return plan
        pytest.fail(f"{request!r} missing from the dataset")

    @pytest.mark.parametrize(
        "greeting", ["hello", "thanks", "namaste", "你好", "bonjour"]
    )
    def test_greetings_are_labelled_none(self, examples, greeting):
        plan = self._plan_for(examples, greeting)
        assert plan["steps"][0]["tool"] == "none"

    @pytest.mark.parametrize("noise", ["lpoe", "jiii", "asdf"])
    def test_unactionable_input_is_labelled_none(self, examples, noise):
        """The exact case that created a file from four random letters."""
        plan = self._plan_for(examples, noise)
        assert plan["steps"][0]["tool"] == "none"

    def test_real_requests_are_not_labelled_none(self, examples):
        """
        A dataset that answers `none` to everything would "fix" the
        reported bug by making Pearl useless. Real requests must map to
        real tools.
        """
        actionable = [
            (text, plan)
            for text, plan in examples
            if plan["steps"][0]["tool"] != "none"
        ]
        assert len(actionable) >= 40, "too few actionable examples to balance"

    def test_dataset_is_not_dominated_by_none(self, examples):
        none_count = sum(
            1 for _, plan in examples if plan["steps"][0]["tool"] == "none"
        )
        ratio = none_count / len(examples)
        assert ratio < 0.6, (
            f"{ratio:.0%} of examples are 'none' — the model would learn to "
            "refuse everything"
        )

    def test_read_requests_map_to_read_file(self, examples):
        plan = self._plan_for(examples, "read hello.py")
        step = plan["steps"][0]
        assert step["tool"] == "read_file"
        assert step["arguments"]["path"] == "hello.py"

    def test_multi_step_plans_use_valid_dependency_ids(self, examples):
        """
        A depends_on naming a tool instead of a step id is what produced
        the dependency-cycle failures. The dataset must not teach it.
        """
        for text, plan in examples:
            ids = {s["id"] for s in plan["steps"] if "id" in s}
            for step in plan["steps"]:
                for dep in step.get("depends_on", []):
                    assert dep in ids, f"{text!r}: depends_on {dep!r} names no step id"


class TestPromptRendering:
    def test_uses_pearls_real_planning_prompt(self, tmp_path):
        """
        Training on a paraphrase teaches a mapping the model is never
        shown at inference, so the fine-tune would not transfer.
        """
        prompt = render_prompt("read hello.py", str(tmp_path))

        assert "Pearl's planning engine" in prompt
        assert "read hello.py" in prompt
        # The tool list must be present, or the model cannot learn which
        # names are valid.
        assert "read_file" in prompt

    def test_workspace_argument_reaches_the_prompt(self, tmp_path):
        """
        build_prompt reads the workspace from thread-local state rather
        than an argument, so a rendered prompt would otherwise carry
        whatever directory the generator ran from — training the model
        on paths from the wrong machine.

        Compared as a resolved path: the value is normalised on the way
        in, so asserting the literal string is platform-dependent.
        """
        from pathlib import Path

        prompt = render_prompt("read hello.py", str(tmp_path))

        assert str(Path(tmp_path).resolve()) in prompt

    def test_workspace_root_is_restored_afterwards(self, tmp_path):
        """Rendering must not leave global state pointing elsewhere."""
        from src.config.workspace import get_workspace_root

        before = get_workspace_root()
        render_prompt("read hello.py", str(tmp_path))

        assert get_workspace_root() == before

    def test_prompt_carries_the_none_rule(self):
        """The rule being taught must appear in the prompt being trained on."""
        prompt = render_prompt("hello", "/workspace")
        assert '"none"' in prompt
