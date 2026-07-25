"""
Shell-command approval and staging for Pearl.

Mirrors `patch_manager.py`'s pending/approve/discard model, but for
shell commands instead of file edits: `CommandApprovalManager`
collects proposed commands instead of running them immediately, and
only runs them once the whole batch is explicitly approved
(`approve_all()`) — or discards it cleanly on rejection
(`discard_all()`). Nothing is executed until one of those happens.

Nothing here knows about tools, the registry, or the executor, and it
never imports `shell_tools` (that would be circular — `shell_tools`
stages commands here). How a request actually runs is injected as a
`runner` callable, so this module only deals in *what* to run and
*when*, not the mechanics of running it — which is also what makes it
straightforward to later swap the default subprocess-based runner for
a container/sandbox-based one without changing this module at all.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True)
class ShellCommandRequest:
    """
    A fully-specified shell command, ready to run (or stage for
    approval first).
    """

    command: str
    cwd: str
    timeout: int
    cpu_seconds: int | None = None
    memory_mb: int | None = None


@dataclass(slots=True)
class PendingCommand:
    """
    One proposed-but-not-yet-run shell command.
    """

    request: ShellCommandRequest


# Injected rather than imported, so this module has no dependency on
# `shell_tools` (which would be circular) and so a future sandboxed/
# containerized runner is a drop-in replacement — nothing here or in
# any caller of `CommandApprovalManager` needs to change.
CommandRunner = Callable[[ShellCommandRequest], subprocess.CompletedProcess]


class CommandApprovalManager:
    """
    Collects proposed shell commands and either runs the whole batch,
    in proposal order, via `runner` (on approval) or discards it (on
    rejection). Nothing is executed until one of those happens.
    """

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner
        self._pending: list[PendingCommand] = []

    def propose(self, request: ShellCommandRequest) -> PendingCommand:
        """
        Stage `request` and return it. Does not run anything.
        """

        pending = PendingCommand(request=request)
        self._pending.append(pending)

        return pending

    @property
    def pending(self) -> list[PendingCommand]:
        """
        Return every currently staged (not yet approved or
        discarded) command, in the order they were proposed.
        """

        return list(self._pending)

    def has_pending(self) -> bool:
        """
        Return whether there are any staged commands awaiting
        approval.
        """

        return bool(self._pending)

    def affected_commands(self) -> list[str]:
        """
        Return the command strings of every currently staged command.
        """

        return [pending.request.command for pending in self._pending]

    def approve_all(self) -> list[subprocess.CompletedProcess]:
        """
        Run every staged command, in proposal order, via `runner`,
        then clear the pending batch. Returns each command's result
        in that same order.

        Never called implicitly: only an explicit approval (via the
        executor's `approve()`) triggers this. If a command raises
        (e.g. a non-zero exit with `check=True`), that exception
        propagates immediately and any commands after it in the batch
        are not run — the same fail-stop behavior a single
        `execute_shell` call already has.
        """

        results = [self._runner(pending.request) for pending in self._pending]

        self._pending.clear()

        return results

    def discard_all(self) -> list[str]:
        """
        Discard every staged command without running anything.
        Returns the list of commands discarded, in proposal order.
        """

        discarded = self.affected_commands()

        self._pending.clear()

        return discarded
