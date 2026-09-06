"""
Patch preview and approval for Pearl's editing tools.

`ChangeManager` collects proposed file edits instead of letting them
write straight to disk, generates a unified diff for each one, and
only writes anything to disk once the whole batch is explicitly
approved (`apply_all()`) — or discards it cleanly on rejection
(`discard_all()`). Nothing here knows about tools, the registry, or
the executor: it only deals in paths and text, so `src/tools/` (this
module) has no dependency on `src/agent/`.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def unified_diff(path: str, original: str | None, updated: str) -> str:
    """
    Return a unified diff between `original` (None for a not-yet-
    existing file) and `updated`, labeled with `path`.
    """

    original_lines = (original or "").splitlines()
    updated_lines = updated.splitlines()

    from_file = "/dev/null" if original is None else f"a/{path}"
    to_file = f"b/{path}"

    diff_lines = difflib.unified_diff(
        original_lines,
        updated_lines,
        fromfile=from_file,
        tofile=to_file,
        lineterm="",
    )

    return "\n".join(diff_lines)


@dataclass(slots=True)
class PendingEdit:
    """
    One proposed-but-not-yet-applied file edit.
    """

    path: str
    original_content: str | None
    updated_content: str
    diff: str

    @property
    def is_new_file(self) -> bool:
        """
        Return whether this edit creates a file that doesn't exist
        yet, as opposed to modifying an existing one.
        """

        return self.original_content is None


class ChangeManager:
    """
    Collects proposed file edits, generates unified diffs for them,
    and either applies the whole batch to disk (on approval) or
    discards it (on rejection). Nothing is written to disk until one
    of those happens.
    """

    def __init__(self) -> None:
        self._pending: list[PendingEdit] = []

    def propose(
        self,
        path: str,
        original_content: str | None,
        updated_content: str,
    ) -> PendingEdit:
        """
        Stage a proposed edit to `path` and return it. Does not
        touch disk.
        """

        edit = PendingEdit(
            path=path,
            original_content=original_content,
            updated_content=updated_content,
            diff=unified_diff(path, original_content, updated_content),
        )

        self._pending.append(edit)

        return edit

    @property
    def pending(self) -> list[PendingEdit]:
        """
        Return every currently staged (not yet applied or
        discarded) edit, in the order they were proposed.
        """

        return list(self._pending)

    def has_pending(self) -> bool:
        """
        Return whether there are any staged edits awaiting approval.
        """

        return bool(self._pending)

    @property
    def is_empty(self) -> bool:
        """
        Return True when no edits are staged, False otherwise.

        Complement of has_pending(); use whichever reads more naturally
        at the call site.
        """

        return not self._pending

    def affected_files(self) -> list[str]:
        """
        Return the paths of every currently staged edit.
        """

        return [edit.path for edit in self._pending]

    def combined_diff(self) -> str:
        """
        Return every staged edit's diff, concatenated in proposal
        order.
        """

        return "\n".join(edit.diff for edit in self._pending)

    def apply_all(self) -> list[str]:
        """
        Write every staged edit to disk atomically, then clear the
        pending batch. Returns the list of files written, in proposal order.

        If any write fails, every successfully written file is restored
        to its pre-apply state (new files are deleted; modified files
        are written back to their original content) before the exception
        propagates. The pending list is NOT cleared on failure so the
        caller can inspect or retry.

        Never called implicitly: only an explicit approval (via the
        executor's `approve()`) triggers this.
        """
        if not self._pending:
            return []

        # Snapshot existing content before touching anything.
        # None means the file does not yet exist (new file).
        snapshots: dict[str, str | None] = {}
        for edit in self._pending:
            p = Path(edit.path)
            snapshots[edit.path] = p.read_text(encoding="utf-8") if p.exists() else None

        applied: list[str] = []
        try:
            for edit in self._pending:
                file_path = Path(edit.path)
                if file_path.parent:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(edit.updated_content, encoding="utf-8")
                applied.append(edit.path)
        except Exception:
            # Rollback every file already written in this batch.
            for path_str in applied:
                prior = snapshots.get(path_str)
                try:
                    p = Path(path_str)
                    if prior is None:
                        p.unlink(missing_ok=True)
                    else:
                        p.write_text(prior, encoding="utf-8")
                except Exception as roll_exc:
                    logger.error(
                        "Rollback failed for %s: %s", path_str, roll_exc
                    )
            raise

        self._pending.clear()
        return applied

    def __len__(self) -> int:
        """Return the number of staged edits."""
        return len(self._pending)

    def __bool__(self) -> bool:
        # Always True — a ChangeManager with zero staged edits is still a
        # valid, usable object.  Without this, Python would derive bool()
        # from __len__() and make an empty manager falsy, which would cause
        # `pm or ChangeManager()` to silently discard the caller's instance.
        return True

    def discard_all(self) -> list[str]:
        """
        Discard every staged edit without writing anything. Returns
        the list of files whose edits were discarded, in proposal
        order.
        """

        discarded = self.affected_files()

        self._pending.clear()

        return discarded
