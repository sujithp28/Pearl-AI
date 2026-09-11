"""
Every production tool must declare what it would do to the workspace.

This suite tests the CATEGORY, not a hand-written list of names. The
repository's recurring defect shape is a spec or test that enumerates
the members of a category and then goes stale the moment the code grows
a new one — an approval gate that listed the write tools, an
architecture rule that listed the packages. So these tests iterate the
real registry: a tool added tomorrow is covered the day it is
registered, and a tool that forgets its tier fails here rather than
being waved through at runtime.

The three tiers:

  safe       read / search / analysis, no workspace change
  staged     a reversible file change that rides the ChangeManager
             approval gate
  dangerous  destructive shell, deletion, irreversible git — always
             needs an explicit human confirmation

Unknown is not a fourth tier. An unclassified tool is treated as
dangerous everywhere it is asked about.
"""

from __future__ import annotations

import pytest

from src.agent.headless import ExecutionPolicy
from src.main import build_registry
from src.tools.metadata import tool
from src.tools.models import Tool, max_risk
from src.tools.registry import ToolRegistry

VALID_TIERS = {"safe", "staged", "dangerous"}


@pytest.fixture(scope="module")
def registry():
    return build_registry()


# ---------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------


def test_every_registered_tool_declares_a_valid_risk_level(registry):
    unclassified = [t.name for t in registry if t.risk_level not in VALID_TIERS]
    assert unclassified == [], (
        "These tools declare no valid risk tier: "
        f"{unclassified}. Add risk_level='safe'|'staged'|'dangerous' to the "
        "@tool decorator."
    )


def test_every_registered_tool_sets_risk_level_explicitly(registry):
    """
    The registry defaults an undeclared tool to "dangerous" so a
    forgotten decorator argument fails closed. That default is a safety
    net, not a way to classify a tool: every production tool states its
    own tier, and a tool that only reads files must not be stuck behind
    a confirmation prompt because nobody said so.
    """
    implicit = [
        t.name
        for t in registry
        if getattr(t.function, "_tool_risk_level", None) is None
    ]
    assert implicit == []


def test_the_registry_holds_tools_in_all_three_tiers(registry):
    tiers = {t.risk_level for t in registry}
    assert tiers == VALID_TIERS


def test_risk_level_is_reported_in_tool_metadata(registry):
    for entry in registry.get_tools():
        assert entry["risk_level"] in VALID_TIERS


# ---------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------


def test_a_tool_registered_without_a_declared_tier_is_dangerous():
    @tool(description="Undeclared.", parameters={})
    def mystery() -> None:  # pragma: no cover - never called
        raise AssertionError("should not run")

    reg = ToolRegistry()
    reg.register(mystery)

    assert reg.get_tool("mystery").risk_level == "dangerous"
    assert reg.get_tool("mystery").is_dangerous()


def test_a_tool_object_built_without_a_tier_is_dangerous():
    assert Tool(name="x", description="", function=lambda: None).risk_level == (
        "dangerous"
    )


def test_a_batch_is_as_risky_as_its_riskiest_member():
    assert max_risk("safe", "staged", "dangerous") == "dangerous"
    assert max_risk("safe", "staged") == "staged"
    assert max_risk("safe") == "safe"
    assert max_risk() == "safe"


# ---------------------------------------------------------------------
# Headless mode cannot bypass a dangerous tool
# ---------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["interactive", "headless", "ci"])
@pytest.mark.parametrize("staged_policy", ["approve", "block"])
def test_no_mode_auto_approves_a_dangerous_tool(mode, staged_policy):
    policy = ExecutionPolicy(mode=mode, staged_policy=staged_policy)
    assert policy.can_auto_approve("dangerous") is False


@pytest.mark.parametrize("staged_policy", ["approve", "block"])
def test_an_unknown_execution_mode_falls_back_to_interactive(staged_policy):
    policy = ExecutionPolicy(mode="yolo", staged_policy=staged_policy)  # type: ignore[arg-type]
    assert policy.mode == "interactive"
    assert policy.can_auto_approve("dangerous") is False
    assert policy.can_auto_approve("staged") is False
    assert policy.can_auto_approve("safe") is False


def test_every_dangerous_production_tool_is_blocked_from_auto_approval(registry):
    """
    Not "the dangerous tools we remembered to list" — every one the
    registry actually holds.
    """
    policy = ExecutionPolicy(mode="headless", staged_policy="approve")
    dangerous = [t.name for t in registry if t.risk_level == "dangerous"]

    assert dangerous, "expected the registry to contain dangerous tools"
    for name in dangerous:
        assert policy.can_auto_approve("dangerous") is False, name


def test_ci_mode_blocks_staged_writes_too():
    policy = ExecutionPolicy(mode="ci", staged_policy="approve")
    assert policy.can_auto_approve("staged") is False
    assert policy.can_auto_approve("safe") is True


def test_approval_reason_explains_a_refused_dangerous_tool():
    policy = ExecutionPolicy(mode="headless", staged_policy="approve")
    assert "confirmation" in policy.approval_reason("dangerous").lower()


# ---------------------------------------------------------------------
# A staged shell command must raise the batch's risk
# ---------------------------------------------------------------------


class TestStagedShellCommandsCountTowardRisk:
    """
    Shell commands stage in CommandApprovalManager, file edits stage in
    ChangeManager. The executor's risk tally used to watch only the
    second one, so a run whose single staged item was an arbitrary
    shell command scored "safe" — and `pearl --yes` in headless mode
    auto-approved it. execute_shell is a dangerous tool; that is the
    whole point of the tier.
    """

    @staticmethod
    def _executor(workspace):
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.executor import AutonomousExecutor
        from src.agent.planner import Planner
        from src.tools.file_tools import read_file, write_file
        from src.tools.shell_tools import execute_shell

        registry = ToolRegistry()
        registry.register(read_file)
        registry.register(write_file)
        registry.register(execute_shell)

        dispatcher = ToolDispatcher(registry)
        planner = Planner(registry, dispatcher)
        # Default command approver: the executor builds the real one,
        # wired to the real runner, exactly as production does.
        return AutonomousExecutor(planner, dispatcher, max_iterations=5)

    @pytest.fixture()
    def workspace(self, tmp_path, monkeypatch):
        from pathlib import Path

        from src.config.workspace import clear_workspace_root, set_workspace_root
        from src.tools.edit_tools import set_active_patch_manager
        from src.tools.shell_tools import set_active_command_approver

        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        set_workspace_root(tmp_path)
        yield tmp_path
        set_active_patch_manager(None)
        set_active_command_approver(None)
        clear_workspace_root()

    def test_a_run_that_stages_only_a_shell_command_is_dangerous(
        self, workspace, monkeypatch
    ):
        executor = self._executor(workspace)
        monkeypatch.setattr(
            executor.planner.client,
            "generate_json",
            lambda prompt, cancel_check=None: {
                "steps": [{"tool": "execute_shell", "arguments": {"command": "ls"}}]
            },
        )

        report = executor.run("list the files")

        assert report.stop_reason == "awaiting_approval"
        assert executor.command_approver.has_pending()
        assert executor.staged_risk_level() == "dangerous"

    def test_headless_auto_approval_refuses_that_run(self, workspace, monkeypatch):
        executor = self._executor(workspace)
        monkeypatch.setattr(
            executor.planner.client,
            "generate_json",
            lambda prompt, cancel_check=None: {
                "steps": [{"tool": "execute_shell", "arguments": {"command": "ls"}}]
            },
        )
        executor.run("list the files")

        policy = ExecutionPolicy(mode="headless", staged_policy="approve")

        assert policy.can_auto_approve(executor.staged_risk_level()) is False

    def test_a_run_that_stages_only_a_file_edit_stays_staged(
        self, workspace, monkeypatch
    ):
        executor = self._executor(workspace)
        target = workspace / "a.txt"
        target.write_text("before", encoding="utf-8")
        monkeypatch.setattr(
            executor.planner.client,
            "generate_json",
            lambda prompt, cancel_check=None: {
                "steps": [
                    {"tool": "read_file", "arguments": {"path": str(target)}},
                    {
                        "tool": "write_file",
                        "arguments": {"path": str(target), "content": "hi\n"},
                    },
                ]
            },
        )

        executor.run("write a file")

        assert executor.staged_risk_level() == "staged"
        policy = ExecutionPolicy(mode="headless", staged_policy="approve")
        assert policy.can_auto_approve(executor.staged_risk_level()) is True


# ---------------------------------------------------------------------
# No tool is decorated and then quietly forgotten
# ---------------------------------------------------------------------


# Tools that carry the @tool decorator but are deliberately NOT offered
# to the agent. Keep this list short and justified: an entry here is a
# capability Pearl has written down and chosen not to give itself.
#
# Adding a tool? Register it in src/main.py:build_registry(). Only add
# a name here if the tool must stay out of the agent's reach, and say
# why.
UNREGISTERED_BY_DESIGN = {
    # Irreversible or history-rewriting git, kept out of the agent's
    # hands; git_restore is the one destructive git tool it does get,
    # and that one is tiered dangerous.
    "git_undo_last",
    # Read-only git helpers superseded by git_status/git_log, which
    # already carry the same information in the agent's context.
    "git_branch",
    "git_is_repo",
    "git_stash_list",
    # File moves/copies the agent can express with read_file +
    # write_file + delete_file, all of which are already gated.
    "copy_file",
    "rename_file",
    # Analysis helpers used by humans and by VerificationEngine, not
    # planned as agent steps.
    "diff_files",
    "lint_file",
    "run_tests",
}


def _decorated_tool_names() -> set[str]:
    import ast
    import pathlib

    names: set[str] = set()
    for path in pathlib.Path("src").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - src must parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                if getattr(target, "id", None) == "tool":
                    names.add(node.name)
    return names


def test_every_decorated_tool_is_registered_or_excluded_on_purpose(registry):
    """
    A @tool-decorated function that nobody registers is unreachable
    production code: it looks like a capability, ships like a
    capability, and can never be called. It also escapes the risk audit
    above, which only sees the registry.

    This test compares the two sets rather than listing either, so a
    tool added tomorrow is covered the day it is written.
    """
    decorated = _decorated_tool_names()
    registered = {t.name for t in registry}

    forgotten = decorated - registered - UNREGISTERED_BY_DESIGN

    assert forgotten == set(), (
        f"These tools are decorated but never registered: {sorted(forgotten)}. "
        "Register them in src/main.py:build_registry(), or add them to "
        "UNREGISTERED_BY_DESIGN with a reason."
    )


def test_the_exclusion_list_has_no_stale_entries():
    """
    An excluded name that no longer exists is a note about code that is
    gone. Catch it here rather than letting the list rot.
    """
    stale = UNREGISTERED_BY_DESIGN - _decorated_tool_names()
    assert stale == set(), f"UNREGISTERED_BY_DESIGN names nothing: {sorted(stale)}"


def test_nothing_is_both_registered_and_excluded(registry):
    registered = {t.name for t in registry}
    assert UNREGISTERED_BY_DESIGN & registered == set()
