"""
Workspace checkpoints — undo for anything Pearl writes.

Pearl's approval gate stops unwanted changes reaching disk, but until
now nothing could undo a change once approved: the only recovery was
the user's own git, and only if they happened to have committed. That
makes every approval decision expensive, because a mistake is
permanent. Cheap rollback is what lets approval stay fast.

A checkpoint is a commit in a *shadow* git repository living outside
the user's project, at `~/.pearl/workspaces/<key>/shadow.git`, keyed
by a hash of the workspace's absolute path. It is a completely
separate repository — the user's own `.git`, staging area, branches,
and history are never touched, and Pearl never runs a mutating
command against them. Restoring a checkpoint changes files in the
working tree; it does not rewrite anyone's commit history.

Storage lives outside the workspace deliberately (not
`<workspace>/.pearl/`, which is where this started). Writing into the
user's project was itself a surprise significant enough to require
editing their `.gitignore` to compensate — a clear signal the
original choice was wrong. A workspace whose earlier Pearl session
left the legacy in-workspace layout behind is migrated once,
automatically, on first use (see `_migrate_legacy_store`).

Two behaviours were found by running the real git mechanics rather
than reasoning about them, and both would have been silent corruption
if shipped as first imagined:

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

Deleting and renaming a checkpoint are handled by a small JSON
metadata sidecar (`metadata.json` in the store), not by rewriting git
history. A git commit is treated as permanent content — immutable by
design, matching git's own model — while the metadata layer is the
mutable, user-facing presentation on top of it: a label that can
change, and a deleted flag that hides a checkpoint from listing and
refuses to restore it. Actually stripping a commit out of the middle
of a linear history requires rewriting every descendant commit, which
is real risk (a botched rewrite corrupts the whole store) for no real
benefit here — these are small text diffs, not large binaries where
reclaiming space would matter. The metadata file is self-healing: if
it goes missing or becomes unreadable, it is rebuilt from `git log`
on next use rather than treated as a fatal error.

Uses the `git` binary via subprocess, exactly as `git_tools.py`
already does, so this adds no dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.config.settings import Settings

logger = logging.getLogger(__name__)

GIT_TIMEOUT = 60

#: Legacy in-workspace directory, checked for one-time migration only.
#: See `_migrate_legacy_store`.
_LEGACY_PEARL_DIR = ".pearl"
_LEGACY_SHADOW_GIT_DIR = f"{_LEGACY_PEARL_DIR}/shadow.git"

#: Committed with a fixed identity so checkpoints work on a machine
#: with no global git config, and are never attributed to the user.
COMMIT_NAME = "Pearl"
COMMIT_EMAIL = "pearl@localhost"

#: Generous upper bound on how many commits are ever read from git in
#: one call. Retention (Settings.CHECKPOINT_MAX_COUNT) keeps the real
#: count far below this in normal operation; this just guarantees
#: `list()`/`latest()`/retention itself never silently see a partial
#: window that makes deleted-filtering produce a short list.
_MAX_GIT_LOG_WINDOW = 2000

#: Metadata sidecar filename, inside the store (next to `shadow.git`).
_METADATA_FILENAME = "metadata.json"


class CheckpointError(RuntimeError):
    """
    Raised when a checkpoint cannot be created, restored, deleted, or
    renamed.
    """


@dataclass(slots=True)
class Checkpoint:
    """
    One snapshot of the workspace.

    `label` is the current display label — from the metadata sidecar
    if renamed, otherwise the commit message it was created with.
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


def _workspace_key(workspace: Path) -> str:
    """
    A short, stable, filesystem-safe identifier for `workspace`.

    Derived from the resolved absolute path so the same workspace
    always maps to the same store, and unrelated workspaces (even
    ones sharing a directory name) never collide.
    """

    return hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:16]


class CheckpointManager:
    """
    Creates, lists, restores, renames, and deletes workspace snapshots
    via a shadow git repo kept outside the workspace.

    Stateless beyond the workspace path: every method shells out to
    git (and reads/writes the small metadata sidecar), so two managers
    over the same workspace see the same checkpoints.
    """

    def __init__(
        self,
        workspace: Path | str | None = None,
        pearl_home: Path | str | None = None,
        max_count: int | None = None,
        max_age_days: int | None = None,
    ) -> None:
        # Resolved via an explicit Path.cwd() call when not given,
        # matching file_tools._ensure_within_workspace and
        # Planner._workspace_root() — never via Path(".").resolve(),
        # which reads the real process os.getcwd() directly and
        # silently ignores a test's `monkeypatch.setattr(Path, "cwd",
        # ...)`. That mismatch was real: it let a checkpoint manager
        # built with the default workspace end up pointed at whatever
        # directory the process actually started in instead of the
        # workspace a test (or a caller) believed it was operating on.
        self.workspace = (
            Path(workspace).resolve() if workspace is not None else Path.cwd().resolve()
        )

        # Injectable rather than reading $HOME directly, so tests can
        # point it at a throwaway directory instead of touching a
        # real developer's ~/.pearl.
        self._pearl_home = (
            Path(pearl_home) if pearl_home is not None else Path.home() / ".pearl"
        )

        self.max_count = (
            max_count if max_count is not None else Settings.CHECKPOINT_MAX_COUNT
        )
        self.max_age_days = (
            max_age_days
            if max_age_days is not None
            else Settings.CHECKPOINT_MAX_AGE_DAYS
        )

    # -- Storage location -------------------------------------------------

    @property
    def _store_root(self) -> Path:
        return self._pearl_home / "workspaces" / _workspace_key(self.workspace)

    @property
    def _git_dir(self) -> Path:
        return self._store_root / "shadow.git"

    @property
    def _metadata_path(self) -> Path:
        return self._store_root / _METADATA_FILENAME

    # -- Internals ----------------------------------------------------------

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

    def _migrate_legacy_store(self) -> None:
        """
        One-time move of a legacy `<workspace>/.pearl/` store (used
        before checkpoints were relocated outside the workspace) to
        the current external location, if the legacy layout exists
        and nothing has been created at the new location yet.

        Uses `shutil.move`, which copies-then-deletes when the source
        and destination are on different filesystems and simply
        renames when they're not — safe either way, and never leaves
        the legacy directory half-moved (a rename is atomic; a
        copy-then-delete fails before the source is removed).
        """

        legacy_dir = self.workspace / _LEGACY_SHADOW_GIT_DIR

        if not legacy_dir.exists() or self._git_dir.exists():
            return

        import shutil

        self._store_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_dir), str(self._git_dir))

        legacy_pearl_dir = self.workspace / _LEGACY_PEARL_DIR

        try:
            if legacy_pearl_dir.exists() and not any(legacy_pearl_dir.iterdir()):
                legacy_pearl_dir.rmdir()
        except OSError:
            pass

        logger.info(
            "Migrated checkpoint store for %s from %s to %s.",
            self.workspace,
            legacy_dir,
            self._git_dir,
        )

    def _ensure_initialised(self) -> None:
        """
        Create the shadow repository if it doesn't exist yet
        (migrating a legacy in-workspace store first, if present), and
        make sure it excludes itself and records which workspace it
        belongs to.
        """

        self._migrate_legacy_store()

        if (self._git_dir / "HEAD").exists():
            self._write_exclude()
            self._write_workspace_marker()
            return

        self._git_dir.mkdir(parents=True, exist_ok=True)

        result = self._run("init", "-q")

        if result.returncode != 0:
            raise CheckpointError(
                f"Could not create the checkpoint store: {result.stderr.strip()}"
            )

        self._write_exclude()
        self._write_workspace_marker()

        logger.info("Initialised checkpoint store at %s", self._git_dir)

    def _write_exclude(self) -> None:
        """
        Keep Pearl's own directory out of every snapshot.

        Relevant even now that the store lives outside the workspace:
        a legacy-migrated store could leave the old name behind, and —
        found by checking rather than assuming it couldn't happen — a
        workspace that happens to be an *ancestor* of `~/.pearl`
        (opening Pearl on `~` itself, or on any directory `$HOME`
        happens to live under) puts the live external store inside the
        very tree being checkpointed. A root-anchored `/.pearl/`
        pattern only protects the case where the store sits exactly at
        the workspace root; it does nothing if the store ends up
        nested deeper than that. So in addition to the legacy name,
        this excludes the store's *actual* resolved location, by its
        real path relative to the workspace, whenever it is inside the
        workspace at all — not only in the one specific shape the
        legacy pattern anticipated.

        `info/exclude` is the shadow repo's private ignore list — the
        user's `.gitignore` is never read from or written to.
        """

        info_dir = self._git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)

        lines = [f"/{_LEGACY_PEARL_DIR}/"]

        if self._store_root.is_relative_to(self.workspace):
            relative = self._store_root.relative_to(self.workspace)
            lines.append(f"/{relative.as_posix()}/")

        (info_dir / "exclude").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_workspace_marker(self) -> None:
        """
        Record the real workspace path the store belongs to, in plain
        text next to the git dir — so the store stays inspectable and
        prunable by hand (`~/.pearl/workspaces/<key>/workspace.txt`)
        even though the directory name itself is an opaque hash.
        """

        marker = self._store_root / "workspace.txt"

        try:
            marker.write_text(f"{self.workspace}\n", encoding="utf-8")
        except OSError:
            pass

    def _has_commits(self) -> bool:
        return self._run("rev-parse", "--verify", "HEAD").returncode == 0

    def _raise_for_git_failure(
        self, result: subprocess.CompletedProcess, action: str
    ) -> None:
        stderr = result.stderr.strip()

        if "index.lock" in stderr:
            raise CheckpointError(
                f"Could not {action}: a checkpoint operation may have been "
                f"interrupted (found a stale lock file). If no other Pearl "
                f"process is running, delete "
                f"'{self._git_dir}/index.lock' and try again.\n{stderr}"
            )

        raise CheckpointError(f"Could not {action}: {stderr}")

    # -- Metadata (labels / soft-delete) -------------------------------------

    def _load_metadata(self) -> dict[str, dict[str, Any]]:
        """
        Read the metadata sidecar, self-healing if it is missing or
        unreadable rather than treating that as fatal — a checkpoint
        store with no metadata file is exactly equivalent to one where
        nothing has ever been renamed or deleted.
        """

        if not self._metadata_path.exists():
            return {}

        try:
            raw = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            checkpoints = raw.get("checkpoints", {})

            if not isinstance(checkpoints, dict):
                raise ValueError("metadata 'checkpoints' is not an object")

            return checkpoints

        except (OSError, ValueError) as exc:
            logger.warning(
                "Checkpoint metadata at %s was unreadable (%s); "
                "continuing as if it were empty.",
                self._metadata_path,
                exc,
            )
            return {}

    def _save_metadata(self, checkpoints: dict[str, dict[str, Any]]) -> None:
        self._store_root.mkdir(parents=True, exist_ok=True)

        payload = json.dumps({"checkpoints": checkpoints}, indent=2)

        # Write to a temp file and rename over the target: a crash
        # mid-write leaves either the old metadata or the new one,
        # never a truncated file that would need self-healing itself.
        tmp_path = self._metadata_path.with_suffix(".json.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self._metadata_path)

    # -- Public API -----------------------------------------------------

    def create(self, label: str) -> Checkpoint | None:
        """
        Snapshot the workspace and return the checkpoint.

        Returns `None` when there is nothing to record (no change
        since the previous checkpoint) rather than creating an empty
        commit, so a list of checkpoints stays meaningful.

        Applies retention (`max_count`/`max_age_days`) after a
        successful commit.
        """

        self._ensure_initialised()

        staged = self._run("add", "-A")

        if staged.returncode != 0:
            self._raise_for_git_failure(staged, "stage the workspace")

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

            self._raise_for_git_failure(committed, "create a checkpoint")

        checkpoint = self.latest()

        if checkpoint is not None:
            logger.info("Created checkpoint %s (%s).", checkpoint.short_id, label)

        self._apply_retention()

        return checkpoint

    def list(self, limit: int = 50, include_deleted: bool = False) -> list[Checkpoint]:
        """
        Return checkpoints, newest first.

        Soft-deleted checkpoints are excluded unless `include_deleted`
        is set.
        """

        checkpoints = self._read_git_log(include_deleted=include_deleted)

        return checkpoints[:limit]

    def _read_git_log(self, include_deleted: bool) -> list[Checkpoint]:
        # Migration must run here too, not only in _ensure_initialised
        # (called by create()) - otherwise a workspace with only
        # legacy checkpoints and no new ones yet would list as empty,
        # looking exactly like every prior checkpoint had vanished.
        self._migrate_legacy_store()

        if not (self._git_dir / "HEAD").exists() or not self._has_commits():
            return []

        result = self._run(
            "log",
            f"-{_MAX_GIT_LOG_WINDOW}",
            "--pretty=format:%H\x1f%s\x1f%aI",
        )

        if result.returncode != 0 or not result.stdout.strip():
            return []

        metadata = self._load_metadata()
        checkpoints: list[Checkpoint] = []

        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 2)

            if len(parts) != 3:
                continue

            commit_id, subject, created_at = parts
            entry = metadata.get(commit_id, {})

            if entry.get("deleted") and not include_deleted:
                continue

            label = entry.get("label", subject)

            checkpoints.append(
                Checkpoint(id=commit_id, label=label, created_at=created_at)
            )

        return checkpoints

    def latest(self) -> Checkpoint | None:
        checkpoints = self.list(limit=1)

        return checkpoints[0] if checkpoints else None

    def rename(self, checkpoint_id: str, label: str) -> Checkpoint:
        """
        Change a checkpoint's display label.

        The underlying git commit message is left exactly as it was
        created (git history is immutable here by design — see the
        module docstring); only the metadata sidecar's label changes,
        which is what `list()`/`latest()` display.
        """

        if not label:
            raise CheckpointError("A checkpoint's label must not be empty.")

        checkpoint = self._require_checkpoint(checkpoint_id)

        metadata = self._load_metadata()
        entry = metadata.setdefault(checkpoint_id, {})
        entry["label"] = label
        self._save_metadata(metadata)

        logger.info("Renamed checkpoint %s to %r.", checkpoint_id[:8], label)

        return Checkpoint(
            id=checkpoint.id, label=label, created_at=checkpoint.created_at
        )

    def delete(self, checkpoint_id: str) -> None:
        """
        Remove a checkpoint from listing and future restores.

        Soft delete only: the underlying git commit is left in place
        (see the module docstring for why) and metadata records it as
        deleted. Deleting an already-deleted or unknown checkpoint
        raises, same as restoring one would.
        """

        self._require_checkpoint(checkpoint_id)

        metadata = self._load_metadata()
        entry = metadata.setdefault(checkpoint_id, {})
        entry["deleted"] = True
        self._save_metadata(metadata)

        logger.info("Deleted checkpoint %s.", checkpoint_id[:8])

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
            self._raise_for_git_failure(
                checked_out, f"restore checkpoint {checkpoint_id[:8]}"
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

    # -- Retention (auto cleanup) --------------------------------------------

    def _apply_retention(self) -> None:
        """
        Soft-delete checkpoints beyond `max_count` and/or older than
        `max_age_days`. Called after every successful `create()`.

        Never touches git history — see the module docstring — so
        this can never corrupt the store, only hide old entries.
        Best-effort: a failure here must not fail the checkpoint that
        was just successfully created.
        """

        try:
            self._apply_retention_unsafe()
        except Exception:
            logger.warning("Checkpoint retention pass failed.", exc_info=True)

    def _apply_retention_unsafe(self) -> None:
        checkpoints = self._read_git_log(include_deleted=False)

        if not checkpoints:
            return

        to_delete: set[str] = set()

        if self.max_count > 0 and len(checkpoints) > self.max_count:
            # Newest-first list: anything beyond the first `max_count`
            # is the oldest excess.
            to_delete.update(c.id for c in checkpoints[self.max_count :])

        if self.max_age_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)

            for checkpoint in checkpoints:
                try:
                    created = datetime.fromisoformat(checkpoint.created_at)
                except ValueError:
                    continue

                if created < cutoff:
                    to_delete.add(checkpoint.id)

        if not to_delete:
            return

        metadata = self._load_metadata()

        for checkpoint_id in to_delete:
            metadata.setdefault(checkpoint_id, {})["deleted"] = True

        self._save_metadata(metadata)

        logger.info("Retention pruned %d checkpoint(s).", len(to_delete))

    # -- Query helpers --------------------------------------------------

    def _require_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        """
        Return the checkpoint if it exists and is not soft-deleted,
        else raise `CheckpointError` with a specific reason.
        """

        self._migrate_legacy_store()

        if not (self._git_dir / "HEAD").exists():
            raise CheckpointError("No checkpoints exist for this workspace yet.")

        verified = self._run("cat-file", "-e", f"{checkpoint_id}^{{commit}}")

        if verified.returncode != 0:
            raise CheckpointError(f"No such checkpoint: {checkpoint_id}")

        metadata = self._load_metadata()

        if metadata.get(checkpoint_id, {}).get("deleted"):
            raise CheckpointError(f"Checkpoint {checkpoint_id[:8]} has been deleted.")

        matches = [
            c for c in self._read_git_log(include_deleted=True) if c.id == checkpoint_id
        ]

        if matches:
            return matches[0]

        # Reachable if the commit exists but fell outside the log
        # window read by _read_git_log (only possible with an
        # extremely long history) — fall back to git directly.
        info = self._run("show", "-s", "--pretty=format:%H\x1f%s\x1f%aI", checkpoint_id)
        commit_id, subject, created_at = info.stdout.split("\x1f", 2)
        label = metadata.get(checkpoint_id, {}).get("label", subject)
        return Checkpoint(id=commit_id, label=label, created_at=created_at)

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
