"""
Repository Scanner — Phase 1 of Pearl's Repository Intelligence (M3).

Responsibilities
----------------
* Walk a repository directory tree.
* Respect ``.gitignore`` files (root-level and nested).
* Always skip well-known noisy/generated directories (see
  :data:`_ALWAYS_IGNORE_DIRS`).
* Collect per-file metadata: path, extension, language, size, mtime,
  MD5 content hash.
* Return a :class:`~models.ScanResult`.

Performance notes
-----------------
* ``os.walk(topdown=True)`` with in-place ``dirnames`` mutation prunes
  entire ignored subtrees — ``node_modules``, ``.git`` etc. are never
  descended into, regardless of repository size.
* ``.gitignore`` rules are compiled to :class:`re.Pattern` objects at
  load time; per-file matching is a handful of regex searches.
* MD5 hashing runs inside a :class:`~concurrent.futures.ThreadPoolExecutor`
  so I/O does not bottleneck the directory walk on large repos.

Usage
-----
::

    from pathlib import Path
    from src.repository.scanner import RepositoryScanner

    scanner = RepositoryScanner(Path("/my/project"))
    result  = scanner.scan()

    for fi in result.files:
        print(fi.relative_path, fi.language)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.repository.models import FileInfo, ScanResult, detect_language

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded directory blacklist
# ---------------------------------------------------------------------------
# These directories are pruned unconditionally before any .gitignore logic
# runs.  They are either generated artefacts (dist, build), dependency trees
# that can contain hundreds of thousands of files (node_modules, venv), or
# VCS internals (.git) that Pearl has no reason to parse.
#
# Order is irrelevant — membership is O(1) via frozenset.

_ALWAYS_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        # Version control
        ".git",
        ".hg",
        ".svn",
        # Python artefacts
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".eggs",
        # Python virtual environments (every popular naming convention)
        "venv",
        ".venv",
        "env",
        ".env",
        # JavaScript / Node
        "node_modules",
        ".npm",
        # Build outputs
        "dist",
        "build",
        ".build",
        "out",
        "target",          # Rust / Maven
        # Caches and tool state
        ".cache",
        ".DS_Store",
        "coverage",
        "htmlcov",
        # IDE directories
        ".idea",
        ".vscode",
        # Misc generated
        "__MACOSX",
        ".next",           # Next.js
        ".nuxt",           # Nuxt.js
        ".svelte-kit",     # SvelteKit
    }
)

# Maximum file size to hash (bytes).  Files larger than this are included
# in the scan result (so the planner knows they exist) but their
# content_hash is left as an empty string.
_DEFAULT_MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB


# ---------------------------------------------------------------------------
# Gitignore rule compilation
# ---------------------------------------------------------------------------


class GitignoreRules:
    """Compiled rules from a single ``.gitignore`` file.

    Rules are stored as ``(pattern, is_negation, dir_only)`` triples.
    The last matching rule wins, which is the standard gitignore
    semantics for ordered rule evaluation.

    Parameters
    ----------
    base:
        The directory that contains the ``.gitignore`` file.  Path
        matching is performed relative to this directory.
    raw_lines:
        Raw text lines from the ``.gitignore`` file (not pre-stripped).
    """

    __slots__ = ("base", "_patterns")

    def __init__(self, base: Path, raw_lines: list[str]) -> None:
        self.base: Path = base.resolve()
        self._patterns: list[tuple[re.Pattern[str], bool, bool]] = [
            compiled
            for line in raw_lines
            if (compiled := _compile_gitignore_pattern(line)) is not None
        ]

    def __len__(self) -> int:
        return len(self._patterns)

    def is_ignored(self, path: Path, *, is_dir: bool) -> bool | None:
        """Check *path* against these rules.

        Returns
        -------
        ``True``
            A rule matched and it is not a negation — path is ignored.
        ``False``
            The last matching rule is a negation — path is *un*-ignored.
        ``None``
            No rule matched — no decision from this ``.gitignore``.

        The caller accumulates results from multiple
        :class:`GitignoreRules` objects; the last non-``None`` result
        across all applicable rules wins.
        """
        try:
            rel_fwd = path.resolve().relative_to(self.base).as_posix()
        except ValueError:
            return None  # path is outside this .gitignore's base directory

        decision: bool | None = None

        for regex, negation, dir_only in self._patterns:
            if dir_only and not is_dir:
                continue
            if regex.search(rel_fwd):
                decision = not negation

        return decision


def _compile_gitignore_pattern(
    raw: str,
) -> tuple[re.Pattern[str], bool, bool] | None:
    """Compile one raw ``.gitignore`` pattern line into a usable triple.

    Returns ``(regex, is_negation, dir_only)`` or ``None`` for blank
    lines and comment lines (both of which are no-ops).

    Supported gitignore features:

    * ``#`` comment lines
    * ``!`` negation prefix
    * Trailing ``/`` (directory-only patterns)
    * Leading ``/`` (anchor pattern to the ``.gitignore`` base)
    * ``*`` — matches any character except ``/``
    * ``?`` — matches any single character except ``/``
    * ``**`` — matches any sequence of characters including ``/``
    * ``**/`` prefix — matches at any directory depth
    """
    line = raw.strip()

    if not line or line.startswith("#"):
        return None

    negation = line.startswith("!")
    if negation:
        line = line[1:].strip()

    if not line:
        return None

    dir_only = line.endswith("/")
    if dir_only:
        line = line[:-1]

    if not line:
        return None

    # A pattern is anchored (matched only relative to the .gitignore
    # directory, not at arbitrary depth) when it contains a slash
    # anywhere other than a trailing one, or starts with a slash.
    anchored = line.startswith("/") or ("/" in line.rstrip("/"))

    if line.startswith("/"):
        line = line[1:]

    # Convert gitignore glob syntax to a Python regex fragment.
    parts: list[str] = []
    i = 0

    while i < len(line):
        if line[i : i + 3] == "**/":
            # Match zero or more path components followed by a separator
            parts.append("(.+/)?")
            i += 3
        elif line[i : i + 2] == "**":
            # Match any sequence (including path separators)
            parts.append(".*")
            i += 2
        elif line[i] == "*":
            # Match any sequence within one path component
            parts.append("[^/]*")
            i += 1
        elif line[i] == "?":
            # Match any single character within one path component
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(line[i]))
            i += 1

    glob_regex = "".join(parts)

    if anchored:
        # Must match from the start of the relative path.
        pattern_str = rf"^{glob_regex}(/|$)"
    else:
        # May match at any path component boundary.
        pattern_str = rf"(^|/){glob_regex}(/|$)"

    try:
        return re.compile(pattern_str), negation, dir_only
    except re.error:
        logger.debug("Skipping malformed gitignore pattern: %r", raw)
        return None


# ---------------------------------------------------------------------------
# Per-file metadata collection (thread-safe, runs in worker pool)
# ---------------------------------------------------------------------------


def _collect_file_info(path: Path, root: Path) -> FileInfo | None:
    """Compute metadata for *path* relative to *root*.

    Designed to be called from a thread pool.  Returns ``None`` on any
    ``OSError`` so the caller can log it and continue.
    """
    try:
        stat = path.stat()  # follows symlinks
    except OSError:
        return None

    content_hash = ""
    if stat.st_size > 0:
        try:
            md5 = hashlib.md5(usedforsecurity=False)
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65_536), b""):
                    md5.update(chunk)
            content_hash = md5.hexdigest()
        except OSError:
            pass  # unreadable — hash stays empty; scan continues

    rel_posix = path.relative_to(root).as_posix()  # forward slashes on all OS

    return FileInfo(
        path=path,
        relative_path=rel_posix,
        extension=path.suffix.lower(),
        language=detect_language(path),
        size=stat.st_size,
        modified_at=stat.st_mtime,
        content_hash=content_hash,
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class RepositoryScanner:
    """Walk a repository and collect per-file metadata.

    Parameters
    ----------
    root:
        Repository root directory.  Must exist and be a directory.
    extra_ignore_dirs:
        Additional directory names to skip unconditionally, merged with
        the built-in :data:`_ALWAYS_IGNORE_DIRS`.
    max_workers:
        Thread-pool size for parallel MD5 hashing.
    max_file_size_bytes:
        Files larger than this are included in the scan result but
        their :attr:`~models.FileInfo.content_hash` is left empty.

    Example
    -------
    ::

        scanner = RepositoryScanner(Path("."))
        result  = scanner.scan()
        python_files = result.source_files(Language.PYTHON)
    """

    def __init__(
        self,
        root: Path,
        extra_ignore_dirs: set[str] | None = None,
        max_workers: int = 8,
        max_file_size_bytes: int = _DEFAULT_MAX_FILE_SIZE_BYTES,
    ) -> None:
        if not root.is_dir():
            raise NotADirectoryError(
                f"Repository root is not a directory: {root}"
            )

        self._root: Path = root.resolve()
        self._ignore_dirs: frozenset[str] = _ALWAYS_IGNORE_DIRS | frozenset(
            extra_ignore_dirs or ()
        )
        self._max_workers = max_workers
        self._max_file_size = max_file_size_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> ScanResult:
        """Perform a full scan of the repository.

        Walk the directory tree, apply ignore rules, collect per-file
        metadata in a thread pool, and return a :class:`~models.ScanResult`.

        The walk is always deterministic: ``dirnames`` and ``filenames``
        are sorted before processing so two successive scans of an
        unchanged repository return the same file order.
        """
        t_start = time.monotonic()
        errors: list[str] = []

        # ----------------------------------------------------------------
        # Phase A: walk the tree, accumulate file paths and .gitignore
        #          rules.  We apply gitignore-based directory pruning as
        #          we walk (prevents descending into large ignored trees),
        #          and file-level gitignore filtering at the same time.
        # ----------------------------------------------------------------

        # gitignore_cache[dir] = compiled rules for that directory's
        # .gitignore file.  Built lazily as directories are visited.
        gitignore_cache: dict[Path, GitignoreRules] = {}
        total_patterns = 0
        file_paths: list[Path] = []

        # Preload the root-level .gitignore so it is available when we
        # process the very first batch of root-level files and subdirs.
        root_gi = self._load_gitignore(self._root)
        if root_gi:
            gitignore_cache[self._root] = root_gi
            total_patterns += len(root_gi)

        for dirpath_str, dirnames, filenames in os.walk(
            self._root, topdown=True, followlinks=False
        ):
            dirpath = Path(dirpath_str).resolve()

            # Load the .gitignore for this directory (skip root; already done).
            if dirpath != self._root:
                gi = self._load_gitignore(dirpath)
                if gi:
                    gitignore_cache[dirpath] = gi
                    total_patterns += len(gi)

            # Rules applicable to items inside *dirpath* = rules from
            # root down to and including *dirpath*.
            active_rules = self._collect_rules(dirpath, gitignore_cache)

            # Prune subdirectories in-place (prevents descent).
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in self._ignore_dirs
                and not self._is_gitignored(
                    dirpath / d, is_dir=True, rules=active_rules
                )
            )

            # Collect eligible files.
            for filename in sorted(filenames):
                fpath = dirpath / filename

                if self._is_gitignored(fpath, is_dir=False, rules=active_rules):
                    continue

                try:
                    if fpath.stat().st_size > self._max_file_size:
                        logger.debug("Skipping oversized file: %s", fpath)
                        continue
                except OSError:
                    continue  # disappeared between walk and stat

                file_paths.append(fpath)

        # ----------------------------------------------------------------
        # Phase B: hash files in parallel.
        # ----------------------------------------------------------------

        results: list[FileInfo] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(_collect_file_info, p, self._root): p
                for p in file_paths
            }
            for future in as_completed(futures):
                fpath = futures[future]
                try:
                    info = future.result()
                    if info is not None:
                        results.append(info)
                except Exception as exc:  # noqa: BLE001
                    msg = f"Error collecting metadata for {fpath}: {exc}"
                    logger.warning(msg)
                    errors.append(msg)

        # Stable sort by relative path (thread pool completion order is
        # non-deterministic even when the file list was sorted).
        results.sort(key=lambda fi: fi.relative_path)

        elapsed_ms = (time.monotonic() - t_start) * 1000

        logger.info(
            "Scan complete: %d files in %.0f ms (gitignore patterns: %d, errors: %d)",
            len(results),
            elapsed_ms,
            total_patterns,
            len(errors),
        )

        return ScanResult(
            root=self._root,
            files=results,
            scan_duration_ms=elapsed_ms,
            gitignore_patterns=total_patterns,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_gitignore(directory: Path) -> GitignoreRules | None:
        """Load and compile the ``.gitignore`` in *directory*, if any."""
        gi_path = directory / ".gitignore"
        if not gi_path.is_file():
            return None
        try:
            lines = gi_path.read_text(encoding="utf-8", errors="replace").splitlines()
            rules = GitignoreRules(directory, lines)
            return rules if len(rules) > 0 else None
        except OSError:
            return None

    def _collect_rules(
        self,
        dirpath: Path,
        cache: dict[Path, GitignoreRules],
    ) -> list[GitignoreRules]:
        """Return all gitignore rules applicable inside *dirpath*.

        Walks from ``self._root`` down to *dirpath* and returns one
        :class:`GitignoreRules` object per ancestor directory that has
        a ``.gitignore`` loaded in *cache*.  The root rules come first;
        more specific (deeper) rules come last, which preserves the
        "more specific rules override less specific ones" semantics when
        :meth:`_is_gitignored` iterates the list.
        """
        collected: list[GitignoreRules] = []
        current = self._root

        if current in cache:
            collected.append(cache[current])

        try:
            rel = dirpath.relative_to(self._root)
        except ValueError:
            return collected  # dirpath outside root — should not happen

        for part in rel.parts:
            current = current / part
            if current in cache:
                collected.append(cache[current])

        return collected

    @staticmethod
    def _is_gitignored(
        path: Path,
        *,
        is_dir: bool,
        rules: list[GitignoreRules],
    ) -> bool:
        """Apply all stacked gitignore rules to *path*.

        The last rule that matches determines the outcome.  If no rule
        matches, the path is not ignored.
        """
        decision: bool | None = None

        for ruleset in rules:
            result = ruleset.is_ignored(path, is_dir=is_dir)
            if result is not None:
                decision = result

        return bool(decision)
