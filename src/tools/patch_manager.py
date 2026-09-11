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


def _dominant_newline(text: str | None) -> str:
    """
    Return the line ending `text` predominantly uses: "\r\n", "\r", or "\n".

    Pearl reads files with universal newlines, so a CRLF file arrives as
    "\n" text and every tool edits it as "\n" text. Writing that back with
    `Path.write_text` then rewrote the whole file's line endings: a CRLF
    file became LF, while the diff the user approved showed only the one
    line that actually changed. On Windows the mirror case applies, because
    text-mode writes translate "\n" to os.linesep there -- so a one-line
    edit to an LF file rewrote every line.

    Either way the bytes on disk did not match the approved diff, which is
    the one thing the approval gate exists to guarantee. Detecting the
    file's own convention and restoring it keeps the change limited to what
    the diff showed.
    """
    if not text:
        return "\n"

    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf

    if crlf >= lf and crlf >= cr and crlf > 0:
        return "\r\n"
    if cr > lf and cr > 0:
        return "\r"
    return "\n"


def _encode_for_disk(text: str, newline: str) -> bytes:
    """
    Encode `text` for disk using `newline`, with no further translation.

    Normalises to "\n" first so content assembled from mixed sources
    cannot leave stray "\r" behind, then applies the target ending once.
    """
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")

    if newline != "\n":
        normalised = normalised.replace("\n", newline)

    return normalised.encode("utf-8")


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

    def apply_all(self) -> list[str]:
        """
        Apply every staged edit to disk atomically, then clear the
        pending batch. Returns the list of files touched, in proposal
        order — writes and deletions alike.

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

        # Snapshot the existing *bytes* before touching anything, so a
        # rollback restores the file exactly — including its line endings,
        # which a text-mode round trip would not preserve.
        # None means the file does not yet exist (new file).
        snapshots: dict[str, bytes | None] = {}
        for edit in self._pending:
            p = Path(edit.path)
            snapshots[edit.path] = p.read_bytes() if p.exists() else None

        applied: list[str] = []
        try:
            for edit in self._pending:
                file_path = Path(edit.path)
                if edit.is_deletion:
                    file_path.unlink(missing_ok=True)
                    applied.append(edit.path)
                    continue
                file_path.parent.mkdir(parents=True, exist_ok=True)
                # Write bytes, not text: text mode translates "\n" to
                # os.linesep, so on Windows every write reflowed the whole
                # file. The newline comes from what was on disk (or, for a
                # new file, from the content itself).
                prior = snapshots[edit.path]
                newline = _dominant_newline(
                    prior.decode("utf-8", errors="replace")
                    if prior is not None
                    else edit.updated_content
                )
                file_path.write_bytes(_encode_for_disk(edit.updated_content, newline))
                applied.append(edit.path)
        except Exception:
            # Rollback every file already written in this batch.
            for path_str in applied:
                prior_bytes = snapshots.get(path_str)
                try:
                    p = Path(path_str)
                    if prior_bytes is None:
                        p.unlink(missing_ok=True)
                    else:
                        p.write_bytes(prior_bytes)
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
