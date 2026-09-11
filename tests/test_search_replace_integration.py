"""
End-to-end proof that the production agent edits through
SearchReplaceEditor.

The editor used to exist with tests of its own and no caller: nothing in
the real editing path ever reached it. These tests drive the whole
production stack — planner plan, AutonomousExecutor run, ChangeManager
staging, approve() — and assert that the edit which lands on disk could
only have come from the editor's matching, not from a plain
``str.replace``.

They also pin the two boundaries the integration must not cross:
nothing reaches disk before approval, and a match Pearl is not
confident about fails instead of guessing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import AutonomousExecutor
from src.agent.planner import Planner
from src.tools.edit_tools import replace_in_file, set_active_patch_manager
from src.tools.file_tools import read_file
from src.tools.registry import ToolRegistry
from tests.conftest import write_lf

ORIGINAL = (
    "class Service:\n"
    "    def login(self, user):\n"
    '        """Log a user in."""\n'
    "        return check(user)\n"
)


@pytest.fixture(autouse=True)
def _no_leaked_staging():
    yield
    set_active_patch_manager(None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from src.config.workspace import set_workspace_root

    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    set_workspace_root(tmp_path)
    return tmp_path


def _build_executor():
    registry = ToolRegistry()
    registry.register(read_file)
    registry.register(replace_in_file)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    return AutonomousExecutor(planner, dispatcher, max_iterations=10), planner


def _plan_of(*steps):
    return lambda prompt, cancel_check=None: {"steps": list(steps)}


def _run_edit(monkeypatch, workspace, search: str, replacement: str):
    """
    Drive a real run that reads the file and then edits it, returning
    (executor, target_path).
    """
    target = workspace / "service.py"
    write_lf(target, ORIGINAL)

    executor, planner = _build_executor()
    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "read_file", "arguments": {"path": str(target)}},
            {
                "tool": "replace_in_file",
                "arguments": {
                    "path": str(target),
                    "search": search,
                    "replacement": replacement,
                },
            },
        ),
    )

    report = executor.run("tighten up the login signature")
    return executor, target, report


def test_agent_edit_survives_spacing_the_model_got_wrong(monkeypatch, workspace):
    """
    The model quotes the signature with spacing the file does not have
    — the single most common way a model's search string misses. A
    plain str.replace finds nothing and the step is a silent no-op.
    Through SearchReplaceEditor the whitespace-normalized strategy
    matches, and a real edit is staged.
    """
    search = "def  login( self, user ):"
    assert search not in ORIGINAL, "this test is only meaningful for a non-exact search"

    executor, target, report = _run_edit(
        monkeypatch,
        workspace,
        search=search,
        replacement="def login(self, user: str) -> bool:",
    )

    assert report.stop_reason == "awaiting_approval"

    # Staged, not written.
    assert target.read_text(encoding="utf-8") == ORIGINAL

    staged = executor.patch_manager.pending[0]
    assert "def login(self, user: str) -> bool:" in staged.updated_content
    assert "def login(self, user):" not in staged.updated_content

    executor.approve()

    on_disk = target.read_text(encoding="utf-8")
    assert "def login(self, user: str) -> bool:" in on_disk
    # Everything the edit did not name is untouched — a minimal edit,
    # not a whole-file replacement.
    assert '"""Log a user in."""' in on_disk
    assert "class Service:" in on_disk
    assert "return check(user)" in on_disk


def test_agent_edit_matches_across_reflowed_whitespace(monkeypatch, workspace):
    """
    The model's quote collapses the newline and indentation between two
    lines. Only the normalized strategy can match that.
    """
    executor, target, report = _run_edit(
        monkeypatch,
        workspace,
        search='"""Log a user in.""" return check(user)',
        replacement='"""Log a user in."""\n        return check(user, strict=True)',
    )

    assert report.stop_reason == "awaiting_approval"
    staged = executor.patch_manager.pending[0]
    assert "check(user, strict=True)" in staged.updated_content

    executor.approve()
    assert "check(user, strict=True)" in target.read_text(encoding="utf-8")


def test_exact_match_still_behaves_exactly_as_before(monkeypatch, workspace):
    executor, target, report = _run_edit(
        monkeypatch,
        workspace,
        search="check(user)",
        replacement="check(user, strict=True)",
    )

    executor.approve()
    assert target.read_text(encoding="utf-8") == ORIGINAL.replace(
        "check(user)", "check(user, strict=True)"
    )


def test_a_search_string_with_no_plausible_match_changes_nothing(
    monkeypatch, workspace
):
    """
    Confidence guard: below the similarity threshold the editor fails
    rather than picking the least-bad region. Nothing is staged, so
    nothing can be approved onto disk.
    """
    executor, target, report = _run_edit(
        monkeypatch,
        workspace,
        search="def completely_unrelated_helper(payload, retries, backoff):",
        replacement="def whatever():",
    )

    assert executor.patch_manager.is_empty
    assert target.read_text(encoding="utf-8") == ORIGINAL


def test_nothing_reaches_disk_before_approval(monkeypatch, workspace):
    """
    The invariant, restated for this edit path specifically.
    """
    executor, target, report = _run_edit(
        monkeypatch,
        workspace,
        search="def login(self, user):",
        replacement="def login(self) -> None:",
    )

    assert executor.patch_manager.has_pending()
    assert target.read_text(encoding="utf-8") == ORIGINAL

    executor.reject()

    assert target.read_text(encoding="utf-8") == ORIGINAL
    assert executor.patch_manager.is_empty


def test_replace_in_file_reports_the_strategy_it_used(workspace):
    """
    The tool's return value is what the planner and reflection see. A
    normalized match must report a real replacement count, not the 0 a
    failed exact match used to return.
    """
    target = workspace / "service.py"
    write_lf(target, ORIGINAL)

    replaced = replace_in_file(
        str(target),
        search="def   login(self,   user):",
        replacement="def login(self, user: str) -> bool:",
    )

    assert replaced == 1
    assert "user: str" in target.read_text(encoding="utf-8")
