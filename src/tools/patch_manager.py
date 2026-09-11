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

from src.tools.file_io import read_text, write_text

logger = logging.getLogger(__name__)


class StaleFileError(RuntimeError):
    """
    Raised when a file changed on disk between the moment an edit was
    staged for review and the moment the approved batch tried to write
    it.

    The user approved a diff against a specific version of the file.
    Writing the staged content anyway would discard whatever changed in
    between — their own save, another tool, a `git checkout`. Pearl
    refuses instead: an aborted apply is recoverable, an overwritten
    edit is not.
    """

    def __init__(self, paths: list[str]) -> None:
        self.paths = list(paths)
        super().__init__(
            "These files changed on disk after the edits were staged for "
            "review, so applying them would overwrite newer content: "
            + ", ".join(self.paths)
            + ". Re-run the change against the current file contents."
        )


def unified_diff(path: str, original: str | None, updated: str | None) -> str:
    """
    Return a unified diff between `original` (None for a not-yet-
    existing file) and `updated` (None for a deletion), labeled with
    `path`.
    """

    original_lines = (original or "").splitlines()
    updated_lines = (updated or "").splitlines()

    from_file = "/dev/null" if original is None else f"a/{path}"
    to_file = "/dev/null" if updated is None else f"b/{path}"

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
    updated_content: str | None
    diff: str

    @property
    def is_new_file(self) -> bool:
        """
        Return whether this edit creates a file that doesn't exist
        yet, as opposed to modifying an existing one.
        """

        return self.original_content is None and self.updated_content is not None

    @property
    def is_deletion(self) -> bool:
        """
        Return whether this edit removes the file rather than writing
        content to it.
        """

        return self.updated_content is None


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

    def propose_deletion(
        self,
        path: str,
        original_content: str | None,
    ) -> PendingEdit:
        """
        Stage the removal of `path` and return it. Does not touch disk.

        Deletions ride the same pending batch as content edits so the
        user reviews everything a run wants to do to their workspace in
        one approval, rather than approving edits in one place and
        removals in another.
        """

        edit = PendingEdit(
            path=path,
            original_content=original_content,
            updated_content=None,
            diff=unified_diff(path, original_content, None),
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

    def _disk_content(self, path: str) -> str | None:
        """
        Return what `path` currently holds on disk, or None when it does
        not exist. Unreadable bytes read as None too: a file Pearl cannot
        read is a file it cannot prove is unchanged.
        """

        file_path = Path(path)

        if not file_path.exists():
            return None

        try:
            return read_text(file_path)
        except (OSError, UnicodeDecodeError):
            return None

    def stale_paths(self) -> list[str]:
        """
        Return every staged path whose file on disk no longer matches the
        content the staged edit was computed against — i.e. every file
        that changed after the user's diff was generated.

        Edits are checked against a running projection rather than raw
        disk, so a batch that edits the same file twice (each edit built
        on the previous one's output) is not mistaken for a conflict.

        An empty list means the whole batch is safe to apply.
        """

        projected: dict[str, str | None] = {}
        stale: list[str] = []

        for edit in self._pending:
            expected = (
                projected[edit.path]
                if edit.path in projected
                else self._disk_content(edit.path)
            )

            if expected != edit.original_content and edit.path not in stale:
                stale.append(edit.path)

            projected[edit.path] = edit.updated_content

        return stale

    def apply_all(self) -> list[str]:
        """
        Apply every staged edit to disk atomically, then clear the
        pending batch. Returns the list of files touched, in proposal
        order — writes and deletions alike.

        Raises `StaleFileError`, before writing anything, when any target
        file changed on disk after its edit was staged. Nothing the user
        did not review is ever overwritten.

        If any step fails, every file already touched in this batch is
        restored to its pre-apply state (new files are deleted; modified
        files are written back to their original content; deleted files
        are written back from their snapshot) before the exception
        propagates. The pending list is NOT cleared on failure so the
        caller can inspect or retry.

        Never called implicitly: only an explicit approval (via the
        executor's `approve()`) triggers this.
        """
        if not self._pending:
            return []

        # Time-of-check/time-of-use gate. The diff the user approved was
        # computed against `original_content`; if the file has moved on
        # since, applying would silently discard whoever wrote it. Check
        # the whole batch before writing any of it, so an abort leaves
        # the workspace exactly as it was.
        stale = self.stale_paths()
        if stale:
            logger.error("Refusing to apply staged edits over newer content: %s", stale)
            raise StaleFileError(stale)

        # Snapshot existing content before touching anything.
        # None means the file does not yet exist (new file).
        snapshots: dict[str, str | None] = {}
        for edit in self._pending:
            p = Path(edit.path)
            snapshots[edit.path] = read_text(p) if p.exists() else None

        applied: list[str] = []
        try:
            for edit in self._pending:
                file_path = Path(edit.path)
                if edit.is_deletion:
                    file_path.unlink(missing_ok=True)
                    applied.append(edit.path)
                    continue
                if file_path.parent:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                write_text(file_path, edit.updated_content)
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
                        write_text(p, prior)
                except Exception as roll_exc:
                    logger.error("Rollback failed for %s: %s", path_str, roll_exc)
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
