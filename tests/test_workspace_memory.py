from src.agent.executor import ExecutionReport, ExecutionStep
from src.memory.workspace_memory import WorkspaceMemory


# ---------------------------------------------------------------------
# Generic remember / recall
# ---------------------------------------------------------------------


def test_remember_and_recall_round_trip():
    memory = WorkspaceMemory()

    memory.remember("todo", "fix the retry logic")
    memory.remember("todo", "add more tests")

    assert memory.recall("todo") == [
        "fix the retry logic",
        "add more tests",
    ]


def test_recall_unknown_category_returns_empty_list():
    memory = WorkspaceMemory()

    assert memory.recall("nonexistent") == []


# ---------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------


def test_record_file_created_tracks_path():
    memory = WorkspaceMemory()

    memory.record_file_created("src/new_module.py")

    changes = memory.recent_changes()
    assert changes[0]["kind"] == "file_created"
    assert changes[0]["detail"] == "src/new_module.py"


def test_observe_tool_result_recognizes_create_file():
    memory = WorkspaceMemory()

    memory.observe_tool_result(
        "create_file",
        {"path": "src/new_module.py", "content": "x = 1\n"},
        succeeded=True,
    )

    assert memory.summary()["recently_edited_files"] == [
        "src/new_module.py"
    ]
    assert memory.recent_changes()[0]["kind"] == "file_created"


def test_observe_tool_result_recognizes_modify_tools():
    memory = WorkspaceMemory()

    memory.observe_tool_result(
        "replace_in_file",
        {"path": "a.py", "search": "x", "replacement": "y"},
        succeeded=True,
    )

    assert memory.recent_changes()[0]["kind"] == "file_modified"
    assert memory.recent_changes()[0]["detail"] == "a.py"


def test_observe_tool_result_ignores_failed_calls():
    memory = WorkspaceMemory()

    memory.observe_tool_result(
        "create_file", {"path": "a.py"}, succeeded=False
    )

    assert memory.recent_changes() == []


def test_observe_tool_result_ignores_unrecognized_tools():
    memory = WorkspaceMemory()

    memory.observe_tool_result("pwd", {}, succeeded=True, result="/tmp")

    assert memory.recent_changes() == []


# ---------------------------------------------------------------------
# Symbol edits
# ---------------------------------------------------------------------


def test_record_symbol_added_and_removed():
    memory = WorkspaceMemory()

    memory.record_symbol_added("foo", kind="function", file="a.py")
    memory.record_symbol_removed("bar", kind="function", file="a.py")

    changed = memory.changed_symbols()
    assert changed["added"] == [
        {"name": "foo", "kind": "function", "file": "a.py"}
    ]
    assert changed["removed"] == [
        {"name": "bar", "kind": "function", "file": "a.py"}
    ]


def test_observe_tool_result_recognizes_symbol_insert_tools():
    memory = WorkspaceMemory()

    memory.observe_tool_result(
        "insert_after_symbol",
        {"name": "helper", "new_source": "def helper(): ...", "path": "a.py"},
        succeeded=True,
    )

    added = memory.changed_symbols()["added"]
    assert added == [{"name": "helper", "kind": "inserted", "file": "a.py"}]
    assert memory.summary()["recently_created_symbols"] == ["helper"]


def test_observe_tool_result_recognizes_symbol_replace_tools():
    memory = WorkspaceMemory()

    memory.observe_tool_result(
        "replace_function",
        {"name": "foo", "new_source": "def foo(): ...", "path": "a.py"},
        succeeded=True,
    )

    added = memory.changed_symbols()["added"]
    assert added == [{"name": "foo", "kind": "replaced", "file": "a.py"}]


# ---------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------


def test_record_git_operation_tracked_in_recent_changes():
    memory = WorkspaceMemory()

    memory.record_git_operation("git_commit", "Committed a1b2c3d: fix bug")

    change = memory.recent_changes()[0]
    assert change["kind"] == "git_operation"
    assert "git_commit" in change["detail"]
    assert "fix bug" in change["detail"]


def test_observe_tool_result_recognizes_git_tools():
    memory = WorkspaceMemory()

    memory.observe_tool_result(
        "git_commit",
        {"message": "fix bug"},
        succeeded=True,
        result="Committed a1b2c3d: fix bug",
    )

    change = memory.recent_changes()[0]
    assert change["kind"] == "git_operation"
    assert "git_commit" in change["detail"]


def test_observe_tool_result_recognizes_git_create_branch_and_restore():
    memory = WorkspaceMemory()

    memory.observe_tool_result(
        "git_create_branch", {"name": "feature"}, succeeded=True
    )
    memory.observe_tool_result(
        "git_restore", {"files": ["a.py"]}, succeeded=True
    )

    kinds = [c["kind"] for c in memory.recent_changes()]
    assert kinds == ["git_operation", "git_operation"]


# ---------------------------------------------------------------------
# Pending patches
# ---------------------------------------------------------------------


class _FakePatchManager:
    def __init__(self, files):
        self._files = files

    def affected_files(self):
        return list(self._files)


def test_observe_patch_manager_tracks_pending_approvals():
    memory = WorkspaceMemory()
    manager = _FakePatchManager(["a.py", "b.py"])

    memory.observe_patch_manager(manager)

    assert memory.summary()["pending_approvals"] == ["a.py", "b.py"]


def test_record_patches_resolved_clears_pending():
    memory = WorkspaceMemory()
    memory.record_patches_pending(["a.py"])

    memory.record_patches_resolved()

    assert memory.summary()["pending_approvals"] == []


# ---------------------------------------------------------------------
# Completed / failed plans, via ExecutionReport
# ---------------------------------------------------------------------


def _step(tool_name, kwargs, result=None, error=None):
    return ExecutionStep(
        iteration=1,
        tool_name=tool_name,
        kwargs=kwargs,
        result=result,
        error=error,
        summary="",
    )


def test_observe_execution_report_records_completed_plan():
    memory = WorkspaceMemory()
    report = ExecutionReport(
        steps=[_step("create_file", {"path": "a.py"}, result=None)],
        stop_reason="completed",
    )

    memory.observe_execution_report(report, prompt="create a.py")

    assert len(memory._completed_plans) == 1
    assert memory._completed_plans[0].prompt == "create a.py"
    assert memory._completed_plans[0].status == "completed"
    # The successful create_file step was also observed.
    assert memory.summary()["recently_edited_files"] == ["a.py"]


def test_observe_execution_report_records_failed_plan_on_fatal_error():
    memory = WorkspaceMemory()
    report = ExecutionReport(
        steps=[_step("boom", {}, error="kaboom")],
        stop_reason="fatal_error",
    )

    memory.observe_execution_report(report, prompt="do something risky")

    assert len(memory._failed_plans) == 1
    assert memory._failed_plans[0].detail == "fatal_error"
    assert memory._completed_plans == []


def test_observe_execution_report_records_failed_plan_on_cancellation():
    memory = WorkspaceMemory()
    report = ExecutionReport(steps=[], stop_reason="cancelled")

    memory.observe_execution_report(report, prompt="cancel me")

    assert memory._failed_plans[0].detail == "cancelled"


def test_observe_execution_report_does_not_record_plan_when_awaiting_approval():
    memory = WorkspaceMemory()
    report = ExecutionReport(
        steps=[_step("create_file", {"path": "a.py"})],
        stop_reason="awaiting_approval",
    )

    memory.observe_execution_report(report, prompt="create a.py")

    assert memory._completed_plans == []
    assert memory._failed_plans == []
    # Steps that did run are still tracked.
    assert memory.summary()["recently_edited_files"] == ["a.py"]


# ---------------------------------------------------------------------
# recent_changes ordering and limit
# ---------------------------------------------------------------------


def test_recent_changes_returns_newest_first():
    memory = WorkspaceMemory()

    memory.record_file_created("a.py")
    memory.record_file_created("b.py")
    memory.record_file_created("c.py")

    changes = memory.recent_changes()

    assert [c["detail"] for c in changes] == ["c.py", "b.py", "a.py"]


def test_recent_changes_respects_limit():
    memory = WorkspaceMemory()

    for i in range(5):
        memory.record_file_created(f"file{i}.py")

    changes = memory.recent_changes(limit=2)

    assert [c["detail"] for c in changes] == ["file4.py", "file3.py"]


def test_recent_changes_mixes_all_kinds():
    memory = WorkspaceMemory()

    memory.record_file_created("a.py")
    memory.record_symbol_added("foo", kind="function", file="a.py")
    memory.record_git_operation("git_commit", "initial commit")

    kinds = [c["kind"] for c in memory.recent_changes()]

    assert kinds == ["git_operation", "symbol_added", "file_created"]


# ---------------------------------------------------------------------
# Memory clearing
# ---------------------------------------------------------------------


def test_clear_resets_all_tracked_state():
    memory = WorkspaceMemory()

    memory.record_file_created("a.py")
    memory.record_symbol_added("foo", kind="function", file="a.py")
    memory.record_git_operation("git_commit", "msg")
    memory.record_patches_pending(["b.py"])
    memory.remember("todo", "fix the thing")
    memory.observe_execution_report(
        ExecutionReport(steps=[], stop_reason="completed"), prompt="p"
    )

    memory.clear()

    assert memory.recent_changes() == []
    assert memory.changed_symbols() == {"added": [], "removed": []}
    assert memory.recall("todo") == []
    assert memory.summary() == {
        "recently_edited_files": [],
        "recently_created_symbols": [],
        "outstanding_todos": [],
        "pending_approvals": [],
    }
    assert memory._completed_plans == []
    assert memory._failed_plans == []


# ---------------------------------------------------------------------
# Planner summary generation (generate_context)
# ---------------------------------------------------------------------


def test_generate_context_empty_when_nothing_tracked():
    memory = WorkspaceMemory()

    assert memory.generate_context() == ""


def test_generate_context_includes_all_sections():
    memory = WorkspaceMemory()

    memory.record_file_modified("a.py")
    memory.record_symbol_added("foo", kind="function", file="a.py")
    memory.remember("todo", "clean up the imports")
    memory.record_patches_pending(["b.py"])

    context = memory.generate_context()

    assert "Recently edited files: a.py" in context
    assert "Recently created symbols: foo" in context
    assert "Outstanding TODOs: clean up the imports" in context
    assert "Pending approvals: b.py" in context


def test_generate_context_omits_empty_sections():
    memory = WorkspaceMemory()

    memory.record_file_modified("a.py")

    context = memory.generate_context()

    assert "Recently edited files" in context
    assert "Outstanding TODOs" not in context
    assert "Pending approvals" not in context
    assert "Recently created symbols" not in context


def test_planner_plan_includes_workspace_context_when_provided(monkeypatch):
    from src.agent.dispatcher import ToolDispatcher
    from src.agent.planner import Planner
    from src.tools.metadata import tool
    from src.tools.registry import ToolRegistry

    @tool(description="No-op.")
    def noop() -> None:
        return None

    registry = ToolRegistry()
    registry.register(noop)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    captured_prompts = []

    def _fake_generate_json(prompt):
        captured_prompts.append(prompt)
        return {"steps": [{"tool": "none", "arguments": {}}]}

    monkeypatch.setattr(planner.client, "generate_json", _fake_generate_json)

    memory = WorkspaceMemory()
    memory.record_file_modified("a.py")
    memory.remember("todo", "clean up the imports")

    planner.plan("do something", workspace_context=memory.generate_context())

    assert len(captured_prompts) == 1
    assert "Current workspace context" in captured_prompts[0]
    assert "Recently edited files: a.py" in captured_prompts[0]
    assert "Outstanding TODOs: clean up the imports" in captured_prompts[0]
    assert "do something" in captured_prompts[0]


def test_planner_plan_without_workspace_context_is_unaffected(monkeypatch):
    from src.agent.dispatcher import ToolDispatcher
    from src.agent.planner import Planner
    from src.tools.metadata import tool
    from src.tools.registry import ToolRegistry

    @tool(description="No-op.")
    def noop() -> None:
        return None

    registry = ToolRegistry()
    registry.register(noop)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    captured_prompts = []

    def _fake_generate_json(prompt):
        captured_prompts.append(prompt)
        return {"steps": [{"tool": "none", "arguments": {}}]}

    monkeypatch.setattr(planner.client, "generate_json", _fake_generate_json)

    planner.plan("do something")

    assert "Current workspace context" not in captured_prompts[0]
