"""
Session-scoped workspace awareness for Pearl.

Tracks what's happened during the *current* coding session — files
created/modified, symbols added/removed, recent Git operations,
pending patches, and completed/failed plans — so a compact summary
of "what's going on right now" can be produced without re-deriving
it from scratch (e.g. for the Planner).

`WorkspaceMemory` is deliberately independent of everything it
observes: it never imports or modifies `AutonomousExecutor`,
`ChangeManager`, the Git tools, or `SymbolEditor`. Instead, a caller
(e.g. `PearlAgent`) feeds it their already-public outputs —
`ExecutionStep`/`ExecutionReport` objects, a `ChangeManager`'s
`affected_files()` — after the fact. This keeps every protected
module completely untouched while still letting memory update
"automatically" from a single call site per event.

Safety: this is session memory, not the source of truth. It never
persists to disk and is cleared by `clear()` or process exit.
`RepositoryIndex` (`src/tools/repo_tools.py`) remains the
authoritative answer to "what does the codebase actually look like
right now" — WorkspaceMemory only answers "what happened this
session."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ChangeRecord:
    """
    One tracked change — a file touch, a symbol edit, or a Git
    operation — in a single chronologically-sortable shape.
    """

    kind: str
    detail: str
    timestamp: str = field(default_factory=_timestamp)


@dataclass(slots=True)
class PlanRecord:
    """
    The outcome of one autonomous plan run.
    """

    prompt: str
    status: str
    detail: str = ""
    timestamp: str = field(default_factory=_timestamp)


class WorkspaceMemory:
    """
    Session-only tracker of workspace activity, with a generic
    `remember`/`recall` store alongside dedicated `record_*` methods
    for the specific kinds of activity Pearl produces.
    """

    # Tool names recognized by `observe_tool_result`, mapped to the
    # kind of change they represent. Unrecognized tool names are a
    # no-op, so this never needs updating in lockstep with every
    # tool Pearl gains.
    _FILE_CREATE_TOOLS = frozenset({"create_file"})
    _FILE_MODIFY_TOOLS = frozenset(
        {"write_file", "append_file", "replace_in_file", "edit_lines", "patch_file"}
    )
    _SYMBOL_INSERT_TOOLS = frozenset({"insert_after_symbol", "insert_before_symbol"})
    _SYMBOL_REPLACE_TOOLS = frozenset({"replace_function", "replace_class"})
    _GIT_TOOLS = frozenset({"git_commit", "git_create_branch", "git_restore"})

    def __init__(self) -> None:
        self._notes: dict[str, list[Any]] = {}
        self._changes: list[ChangeRecord] = []
        self._files_created: list[str] = []
        self._files_modified: list[str] = []
        self._symbols_added: list[dict[str, str]] = []
        self._symbols_removed: list[dict[str, str]] = []
        self._pending_patches: list[str] = []
        self._completed_plans: list[PlanRecord] = []
        self._failed_plans: list[PlanRecord] = []

    # -- Generic remember/recall ----------------------------------------

    def remember(self, category: str, item: Any) -> None:
        """
        Remember `item` under an arbitrary `category` (e.g. "todo").
        """

        self._notes.setdefault(category, []).append(item)

    def recall(self, category: str) -> list[Any]:
        """
        Return everything remembered under `category`, oldest first.
        """

        return list(self._notes.get(category, []))

    # -- Recording specific event kinds ----------------------------------

    def record_file_created(self, path: str) -> None:
        """
        Record that `path` was newly created this session.
        """

        self._files_created.append(path)
        self._changes.append(ChangeRecord("file_created", path))

    def record_file_modified(self, path: str) -> None:
        """
        Record that `path` was modified this session.
        """

        self._files_modified.append(path)
        self._changes.append(ChangeRecord("file_modified", path))

    def record_symbol_added(
        self, name: str, kind: str = "symbol", file: str = ""
    ) -> None:
        """
        Record that a function/class/method named `name` was added
        (or replaced — the net effect is "this symbol now exists in
        this form") this session.
        """

        self._symbols_added.append({"name": name, "kind": kind, "file": file})
        detail = f"{kind} '{name}'" + (f" in {file}" if file else "")
        self._changes.append(ChangeRecord("symbol_added", detail))

    def record_symbol_removed(
        self, name: str, kind: str = "symbol", file: str = ""
    ) -> None:
        """
        Record that a function/class/method named `name` was removed
        this session.
        """

        self._symbols_removed.append({"name": name, "kind": kind, "file": file})
        detail = f"{kind} '{name}'" + (f" in {file}" if file else "")
        self._changes.append(ChangeRecord("symbol_removed", detail))

    def record_git_operation(self, operation: str, detail: str = "") -> None:
        """
        Record a Git operation (commit, branch creation, restore, ...).
        """

        text = operation + (f": {detail}" if detail else "")
        self._changes.append(ChangeRecord("git_operation", text))

    def record_patches_pending(self, files: list[str]) -> None:
        """
        Record the current set of files with a patch awaiting
        approval (replaces any previously tracked set).
        """

        self._pending_patches = list(files)

    def record_patches_resolved(self) -> None:
        """
        Clear pending-patch tracking (call after approval or
        rejection).
        """

        self._pending_patches = []

    def record_plan_completed(self, prompt: str, detail: str = "") -> None:
        """
        Record that an autonomous plan run completed successfully.
        """

        self._completed_plans.append(PlanRecord(prompt, "completed", detail))

    def record_plan_failed(self, prompt: str, detail: str = "") -> None:
        """
        Record that an autonomous plan run did not complete
        successfully (fatal error, max iterations, cancelled, or
        rejected).
        """

        self._failed_plans.append(PlanRecord(prompt, "failed", detail))

    # -- Automatic observation of tool / executor output -----------------

    def observe_tool_result(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        succeeded: bool,
        result: Any = None,
    ) -> None:
        """
        Update tracked state from the outcome of a single tool call
        (e.g. one `ExecutionStep`). A no-op for tools this doesn't
        recognize, or for a failed call.
        """

        if not succeeded:
            return

        path = kwargs.get("path", "")

        if tool_name in self._FILE_CREATE_TOOLS:
            if path:
                self.record_file_created(path)
        elif tool_name in self._FILE_MODIFY_TOOLS:
            if path:
                self.record_file_modified(path)
        elif tool_name in self._SYMBOL_INSERT_TOOLS:
            name = kwargs.get("name", "")
            if name:
                self.record_symbol_added(name, kind="inserted", file=path)
        elif tool_name in self._SYMBOL_REPLACE_TOOLS:
            name = kwargs.get("name", "")
            if name:
                self.record_symbol_added(name, kind="replaced", file=path)
        elif tool_name in self._GIT_TOOLS:
            self.record_git_operation(
                tool_name, str(result) if result is not None else ""
            )

    def observe_patch_manager(self, patch_manager: Any) -> None:
        """
        Sync pending-patch tracking with a `ChangeManager`'s current
        state. Read-only: only calls its existing public
        `affected_files()`, never mutates it.
        """

        self.record_patches_pending(patch_manager.affected_files())

    def observe_execution_report(self, report: Any, prompt: str = "") -> None:
        """
        Update tracked state from a full `ExecutionReport`: every
        step it contains, plus whether the run completed or failed.

        Pending-patch tracking for an `awaiting_approval` report is
        handled separately via `observe_patch_manager`, since the
        patch details live on the `ChangeManager`, not the report.
        """

        for step in report.steps:
            self.observe_tool_result(
                step.tool_name, step.kwargs, step.succeeded, step.result
            )

        if report.stop_reason == "completed":
            self.record_plan_completed(prompt, detail=f"{len(report.steps)} step(s)")
        elif report.stop_reason in (
            "max_iterations",
            "fatal_error",
            "cancelled",
            "rejected",
        ):
            self.record_plan_failed(prompt, detail=report.stop_reason)

    # -- Querying ---------------------------------------------------------

    def recent_changes(self, limit: int = 10) -> list[dict[str, str]]:
        """
        Return the most recent changes (files, symbols, Git
        operations) across the whole session, newest first.
        """

        recent = self._changes[-limit:]

        return [
            {
                "kind": change.kind,
                "detail": change.detail,
                "timestamp": change.timestamp,
            }
            for change in reversed(recent)
        ]

    def changed_symbols(self) -> dict[str, list[dict[str, str]]]:
        """
        Return every symbol added and removed this session.
        """

        return {
            "added": list(self._symbols_added),
            "removed": list(self._symbols_removed),
        }

    def summary(self) -> dict[str, Any]:
        """
        Return a structured snapshot of session activity: recently
        edited files, recently created symbols, outstanding TODOs,
        and pending approvals.
        """

        edited_files = list(dict.fromkeys(self._files_modified + self._files_created))

        return {
            "recently_edited_files": edited_files[-10:],
            "recently_created_symbols": [
                entry["name"] for entry in self._symbols_added
            ][-10:],
            "outstanding_todos": self.recall("todo"),
            "pending_approvals": list(self._pending_patches),
        }

    def generate_context(self) -> str:
        """
        Render `summary()` as a compact block of text suitable for
        `Planner.plan(..., workspace_context=...)`. Empty sections
        are omitted; returns "" if there's nothing to report.
        """

        data = self.summary()
        lines: list[str] = []

        if data["recently_edited_files"]:
            lines.append(
                "Recently edited files: " + ", ".join(data["recently_edited_files"])
            )

        if data["recently_created_symbols"]:
            lines.append(
                "Recently created symbols: "
                + ", ".join(data["recently_created_symbols"])
            )

        if data["outstanding_todos"]:
            lines.append(
                "Outstanding TODOs: "
                + "; ".join(str(item) for item in data["outstanding_todos"])
            )

        if data["pending_approvals"]:
            lines.append("Pending approvals: " + ", ".join(data["pending_approvals"]))

        return "\n".join(lines)

    def clear(self) -> None:
        """
        Reset all tracked session state.
        """

        self._notes.clear()
        self._changes.clear()
        self._files_created.clear()
        self._files_modified.clear()
        self._symbols_added.clear()
        self._symbols_removed.clear()
        self._pending_patches.clear()
        self._completed_plans.clear()
        self._failed_plans.clear()
