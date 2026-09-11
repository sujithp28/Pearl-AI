"""
Tiered tool risk: the classification itself, and the policy that reads it.

Pearl decides what may run without a human by asking two questions: how
dangerous is this tool, and what mode are we in. Both halves existed
before this suite; what did not exist was any guarantee that the first
question has an answer for every tool.

Forty of Pearl's fifty-seven tools declared no tier at all and silently
inherited a middle one, including every shell tool. `test_every_tool`
is the guard against that returning: a new tool that forgets to declare
its tier fails here rather than quietly becoming auto-approvable.

The default is now the most restrictive tier rather than the middle one.
An unclassified tool is a tool nobody has thought about, and the safe
assumption about an unexamined tool is not "probably fine".
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.agent.headless import ExecutionPolicy, get_execution_policy
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry

TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "tools"
VALID_TIERS = {"safe", "staged", "dangerous"}


def _declared_tiers() -> dict[str, str | None]:
    """Every @tool function in src/tools, mapped to its declared tier."""
    found: dict[str, str | None] = {}
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "tool"
                ):
                    continue
                declared = next(
                    (kw.value.value for kw in dec.keywords if kw.arg == "risk_level"),
                    None,
                )
                found[f"{path.name}:{node.name}"] = declared
    return found


class TestEveryToolIsClassified:
    def test_every_tool_declares_a_tier_explicitly(self) -> None:
        """
        Relying on the default is not classification. It means nobody
        decided, and a tool nobody decided about should not be able to
        reach auto-approval by accident.
        """
        undeclared = [name for name, tier in _declared_tiers().items() if tier is None]

        assert undeclared == [], (
            "these tools declare no risk_level and fall back to the default: "
            + ", ".join(undeclared)
        )

    def test_every_declared_tier_is_a_real_tier(self) -> None:
        bad = {
            name: tier
            for name, tier in _declared_tiers().items()
            if tier not in VALID_TIERS
        }

        assert bad == {}

    def test_the_inventory_is_not_empty(self) -> None:
        """Guards the two tests above: both pass trivially on no tools."""
        assert len(_declared_tiers()) > 40


class TestFailClosed:
    def test_a_tool_that_omits_its_tier_is_dangerous(self) -> None:
        @tool(description="Does something nobody classified.")
        def unclassified() -> None: ...

        assert unclassified._tool_risk_level == "dangerous"

    def test_an_undecorated_function_cannot_be_registered_at_all(self) -> None:
        """
        Stronger than a fail-closed default: the registry refuses it.
        Asserted here so that relaxing the check later has to break a
        test rather than silently admitting unclassified callables.
        """
        registry = ToolRegistry()

        def bare() -> None: ...

        with pytest.raises(TypeError):
            registry.register(bare)

    def test_a_tool_missing_its_risk_attribute_registers_as_dangerous(self) -> None:
        """
        The registry's getattr fallback. Unreachable through the normal
        decorator, which always sets the attribute, so it is exercised
        directly: the fallback exists precisely for the abnormal case
        and must not be the permissive tier.
        """
        registry = ToolRegistry()

        @tool(description="Tier attribute removed after decoration.")
        def stripped() -> None: ...

        del stripped._tool_risk_level
        registry.register(stripped)

        assert registry.get_tool("stripped").risk_level == "dangerous"

    def test_an_unknown_execution_mode_falls_back_to_interactive(self) -> None:
        policy = ExecutionPolicy(mode="banana", staged_policy="approve")  # type: ignore[arg-type]

        assert policy.mode == "interactive"
        assert policy.can_auto_approve("safe") is False


class TestRegistryCarriesRisk:
    def test_the_declared_tier_reaches_the_registered_tool(self) -> None:
        registry = ToolRegistry()

        @tool(description="Reads only.", risk_level="safe")
        def peek() -> None: ...

        registry.register(peek)

        assert registry.get_tool("peek").risk_level == "safe"
        assert registry.get_tool("peek").is_safe() is True
        assert registry.get_tool("peek").is_dangerous() is False


class TestClassificationIsCorrect:
    """
    Spot-checks that the tiers describe reality, not just that a string
    is present. Chosen to cover one tool of each kind.
    """

    @pytest.mark.parametrize(
        "name,expected",
        [
            # Reads and analysis mutate nothing.
            ("file_tools.py:read_file", "safe"),
            ("repo_tools.py:search_text", "safe"),
            ("git_tools.py:git_status", "safe"),
            ("symbol_editor.py:find_function", "safe"),
            # Writes that route through the approval gate.
            ("edit_tools.py:create_file", "staged"),
            ("file_tools.py:write_file", "staged"),
            ("symbol_editor.py:replace_function", "staged"),
            # Arbitrary execution and irreversible operations.
            ("shell_tools.py:execute_shell", "dangerous"),
            ("shell_tools.py:run_python", "dangerous"),
            ("file_tools.py:delete_file", "dangerous"),
            ("git_tools.py:git_undo_last", "dangerous"),
            # Mutate the workspace WITHOUT the gate. Unreachable today
            # because they are unregistered; labelled so that registering
            # one cannot quietly open a hole.
            ("file_tools.py:rename_file", "dangerous"),
            ("file_tools.py:copy_file", "dangerous"),
        ],
    )
    def test_tool_tier(self, name: str, expected: str) -> None:
        assert _declared_tiers()[name] == expected


class TestExecutionPolicy:
    """
    The full mode-by-tier matrix. Written out rather than generated so a
    change in policy shows up as a changed expectation, not as a changed
    rule that quietly changes many answers at once.
    """

    @pytest.mark.parametrize(
        "mode,staged_policy,tier,expected",
        [
            # Interactive: a human is present, so nothing auto-approves.
            ("interactive", "approve", "safe", False),
            ("interactive", "approve", "staged", False),
            ("interactive", "approve", "dangerous", False),
            # Headless: reads are free, writes follow the staged policy.
            ("headless", "approve", "safe", True),
            ("headless", "approve", "staged", True),
            ("headless", "block", "staged", False),
            ("headless", "approve", "dangerous", False),
            # CI: reads are free, writes are blocked regardless.
            ("ci", "approve", "safe", True),
            ("ci", "approve", "staged", False),
            ("ci", "block", "staged", False),
            ("ci", "approve", "dangerous", False),
        ],
    )
    def test_matrix(self, mode, staged_policy, tier, expected) -> None:
        policy = ExecutionPolicy(mode=mode, staged_policy=staged_policy)

        assert policy.can_auto_approve(tier) is expected

    @pytest.mark.parametrize("mode", ["interactive", "headless", "ci"])
    @pytest.mark.parametrize("staged_policy", ["approve", "block"])
    def test_dangerous_never_auto_approves_in_any_configuration(
        self, mode, staged_policy
    ) -> None:
        """
        The one rule that must hold no matter how Pearl is configured.
        Headless and CI exist to remove prompts, not protections.
        """
        policy = ExecutionPolicy(mode=mode, staged_policy=staged_policy)

        assert policy.can_auto_approve("dangerous") is False

    def test_every_decision_carries_a_reason(self) -> None:
        """A refusal the user cannot explain is a bug report waiting."""
        policy = ExecutionPolicy(mode="ci", staged_policy="block")

        for tier in VALID_TIERS:
            assert policy.approval_reason(tier).strip()

    def test_the_default_policy_is_interactive(self, monkeypatch) -> None:
        monkeypatch.delenv("PEARL_EXECUTION_MODE", raising=False)
        from src.config import settings as settings_module

        monkeypatch.setattr(settings_module.Settings, "EXECUTION_MODE", "interactive")
        monkeypatch.setattr(settings_module.Settings, "HEADLESS_STAGED_POLICY", "block")

        assert get_execution_policy().is_interactive is True
