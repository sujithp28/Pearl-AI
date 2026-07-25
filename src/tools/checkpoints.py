"""
Workspace checkpoints — undo for anything Pearl writes.

Pearl's approval gate stops unwanted changes reaching disk, but until
now nothing could undo a change once approved: the only recovery was
the user's own git, and only if they happened to have committed. That
makes every approval decision expensive, because a mistake is
permanent. Cheap rollback is what lets approval stay fast.

A checkpoint is a commit in a *shadow* git repository living at
`<workspace>/.pearl/shadow.git`. It is a completely separate
repository — the user's own `.git`, staging area, branches, and
history are never touched, and Pearl never runs a mutating command
against them. Restoring a checkpoint changes files in the working
tree; it does not rewrite anyone's commit history.

Uses the `git` binary via subprocess, exactly as `git_tools.py`
already does, so this adds no dependency.

Two behaviours here were found by running the real thing rather than
reasoning about it, and both would have been silent corruption:

- The shadow repo must exclude itself. Without that it snapshots its
  own object store and `index.lock`, which grows without bound and
  then corrupts the index on the first restore ("index file smaller
  than expected"). Handled via the shadow repo's own `info/exclude`,
  which is invisible to the user's repository and never edits their
  `.gitignore`.
- `git checkout <sha> -- .` restores modified and deleted files but
  leaves files created *after* the checkpoint in place — so it alone
  cannot undo "Pearl created a file", which is the single most common
  thing to want undone. Those are removed explicitly, and reported.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

GIT_TIMEOUT = 60

#: Directory Pearl keeps its own state in, relative to the workspace.
PEARL_DIR = ".pearl"

#: The shadow repository, inside `PEARL_DIR`.
SHADOW_GIT_DIR = f"{PEARL_DIR}/shadow.git"

#: Committed with a fixed identity so checkpoints work on a machine
#: with no global git config, and are never attributed to the user.
COMMIT_NAME = "Pearl"
COMMIT_EMAIL = "pearl@localhost"


class CheckpointError(RuntimeError):
    """
    Raised when a checkpoint cannot be created or restored.
    """


@dataclass(slots=True)
class Checkpoint:
    """
    One snapshot of the workspace.
    """

    id: str
    label: str
    created_at: str

    @property
    def short_id(self) -> str:
        return self.id[:8]


@dataclass(slots=True)
class RestoreReport:
    """
    What a restore actually did, so a caller can show the user
    exactly what changed rather than just claiming success.
    """

    checkpoint_id: str
    restored: list[str]
    removed: list[str]

    @property
    def changed_anything(self) -> bool:
        return bool(self.restored or self.removed)


class CheckpointManager:
    """
    Creates and restores workspace snapshots via a shadow git repo.

    Stateless beyond the workspace path: every method shells out to
    git, so two managers over the same workspace see the same
    checkpoints.
    """

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    # -- Internals ------------------------------------------------------

    @property
    def _git_dir(self) -> Path:
        return self.workspace / SHADOW_GIT_DIR

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        """
        Run one git command against the shadow repository.

        Never raises on a non-zero exit: callers inspect the result so
        they can raise `CheckpointError` with useful context instead
        of a bare `CalledProcessError`.
        """

        return subprocess.run(
            [
                "git",
                f"--git-dir={self._git_dir}",
                f"--work-tree={self.workspace}",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            cwd=str(self.workspace),
        )

    def _ensure_initialised(self) -> None:
        """
        Create the shadow repository if it doesn't exist yet, and make
        sure it excludes itself.
        """

        if (self._git_dir / "HEAD").exists():
            self._write_exclude()
            return

        self._git_dir.mkdir(parents=True, exist_ok=True)

        result = self._run("init", "-q")

        if result.returncode != 0:
            raise CheckpointError(
                f"Could not create the checkpoint store: {result.stderr.strip()}"
            )

        self._write_exclude()

        logger.info("Initialised checkpoint store at %s", self._git_dir)

    def _write_exclude(self) -> None:
        """
        Keep Pearl's own directory out of every snapshot.

        Without this the shadow repo commits its own object store and
        lock files, then corrupts its index on the first restore.
        `info/exclude` is the shadow repo's private ignore list — the
        user's `.gitignore` is never read from or written to.
        """

        info_dir = self._git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)

        (info_dir / "exclude").write_text(f"/{PEARL_DIR}/\n", encoding="utf-8")

    def _has_commits(self) -> bool:
        return self._run("rev-parse", "--verify", "HEAD").returncode == 0

    # -- Public API -----------------------------------------------------

    def create(self, label: str) -> Checkpoint | None:
        """
        Snapshot the workspace and return the checkpoint.

        Returns `None` when there is nothing to record (no change
        since the previous checkpoint) rather than creating an empty
        commit, so a list of checkpoints stays meaningful.
        """

        self._ensure_initialised()

        staged = self._run("add", "-A")

        if staged.returncode != 0:
            raise CheckpointError(
                f"Could not stage the workspace: {staged.stderr.strip()}"
            )

        committed = self._run(
            "-c",
            f"user.name={COMMIT_NAME}",
            "-c",
            f"user.email={COMMIT_EMAIL}",
            "commit",
            "-q",
            "-m",
            label,
        )

        if committed.returncode != 0:
            combined = f"{committed.stdout}{committed.stderr}"

            if "nothing to commit" in combined:
                logger.info("No workspace changes to checkpoint.")
                return None

            raise CheckpointError(f"Could not create a checkpoint: {combined.strip()}")

        checkpoint = self.latest()

        if checkpoint is not None:
            logger.info("Created checkpoint %s (%s).", checkpoint.short_id, label)

        return checkpoint

    def list(self, limit: int = 50) -> list[Checkpoint]:
        """
        Return checkpoints, newest first.
        """

        if not (self._git_dir / "HEAD").exists() or not self._has_commits():
            return []

        result = self._run(
            "log",
            f"-{limit}",
            "--pretty=format:%H\x1f%s\x1f%cI",
        )

        if result.returncode != 0 or not result.stdout.strip():
            return []

        checkpoints: list[Checkpoint] = []

        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 2)

            if len(parts) != 3:
                continue

            checkpoints.append(
                Checkpoint(id=parts[0], label=parts[1], created_at=parts[2])
            )

        return checkpoints

    def latest(self) -> Checkpoint | None:
        checkpoints = self.list(limit=1)

        return checkpoints[0] if checkpoints else None

    def preview_restore(self, checkpoint_id: str) -> RestoreReport:
        """
        Report what `restore` would change, without changing anything.

        Exists because restoring *removes* files created since the
        checkpoint. Pearl shows a diff before writing; it should show
        this before deleting, for the same reason.
        """

        self._require_checkpoint(checkpoint_id)

        return RestoreReport(
            checkpoint_id=checkpoint_id,
            restored=self._files_differing_from(checkpoint_id),
            removed=self._files_added_since(checkpoint_id),
        )

    def restore(self, checkpoint_id: str) -> RestoreReport:
        """
        Return the workspace to the state captured by `checkpoint_id`.

        Restores modified and deleted files, and removes files created
        since — `git checkout` alone does not do the last part, so
        "Pearl created a file" would otherwise be un-undoable.
        """

        self._require_checkpoint(checkpoint_id)

        report = self.preview_restore(checkpoint_id)

        checked_out = self._run("checkout", checkpoint_id, "--", ".")

        if checked_out.returncode != 0:
            raise CheckpointError(
                f"Could not restore checkpoint {checkpoint_id[:8]}: "
                f"{checked_out.stderr.strip()}"
            )

        for relative_path in report.removed:
            target = self.workspace / relative_path

            try:
                target.unlink()
            except OSError as exc:
                logger.warning("Could not remove %s: %s", relative_path, exc)

        logger.info(
            "Restored checkpoint %s: %d file(s) reverted, %d removed.",
            checkpoint_id[:8],
            len(report.restored),
            len(report.removed),
        )

        return report

    # -- Query helpers --------------------------------------------------

    def _require_checkpoint(self, checkpoint_id: str) -> None:
        if not (self._git_dir / "HEAD").exists():
            raise CheckpointError("No checkpoints exist for this workspace yet.")

        verified = self._run("cat-file", "-e", f"{checkpoint_id}^{{commit}}")

        if verified.returncode != 0:
            raise CheckpointError(f"No such checkpoint: {checkpoint_id}")

    def _files_differing_from(self, checkpoint_id: str) -> list[str]:
        """
        Tracked files whose content differs from the checkpoint (or
        which have been deleted since).
        """

        result = self._run("diff", "--name-only", checkpoint_id)

        if result.returncode != 0:
            return []

        return [line for line in result.stdout.splitlines() if line]

    def _files_added_since(self, checkpoint_id: str) -> list[str]:
        """
        Files present now but absent from the checkpoint.

        Covers both files git already tracks (added in a later
        checkpoint) and ones never checkpointed at all, so a restore
        genuinely returns the workspace to how it looked.
        """

        in_checkpoint = set()

        listed = self._run("ls-tree", "-r", "--name-only", checkpoint_id)

        if listed.returncode == 0:
            in_checkpoint = {line for line in listed.stdout.splitlines() if line}

        present: set[str] = set()

        for args in (
            ("ls-files", "--cached"),
            ("ls-files", "--others", "--exclude-standard"),
        ):
            result = self._run(*args)

            if result.returncode == 0:
                present.update(line for line in result.stdout.splitlines() if line)

        return sorted(present - in_checkpoint)
