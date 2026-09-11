"""
Search-replace editor for Pearl V2.

Pearl previously relied on whole-file replacement.  This module adds
structured, incremental editing with confidence-guarded matching.

Matching strategies (tried in order):
  1. Exact match
  2. Whitespace-normalized match
  3. Fuzzy match (configurable similarity threshold)

If confidence is insufficient: FAIL.  The caller (planner/reflection)
must handle recovery — never silently modify the wrong region.

All edits are staged through ChangeManager so approval is preserved.

Usage::

    from src.tools.search_replace_editor import SearchReplaceEditor

    editor = SearchReplaceEditor()
    result = editor.apply(
        path="src/auth/login.py",
        search="def login(user):",
        replace="def login(user: str) -> bool:",
    )
    if not result.succeeded:
        raise PatchError(result.error)
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.config.workspace import get_workspace_root
from src.tools.edit_tools import get_active_patch_manager
from src.tools.file_io import read_text, write_text
from src.tools.file_tools import _ensure_within_workspace
from src.tools.patch_manager import ChangeManager

logger = logging.getLogger(__name__)

MatchStrategy = Literal["exact", "normalized", "fuzzy"]

# Below this similarity ratio the fuzzy match is rejected.
_DEFAULT_SIMILARITY_THRESHOLD = 0.85


@dataclass(slots=True)
class EditResult:
    succeeded: bool
    path: str
    strategy: MatchStrategy | None
    similarity: float
    occurrences: int
    error: str = ""


class MatchFailed(ValueError):
    """Raised when no sufficiently similar match is found."""


def _normalize_ws(text: str) -> str:
    """Collapse runs of whitespace/newlines to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _find_exact(content: str, search: str) -> int:
    """Return start index of first exact occurrence, or -1."""
    return content.find(search)


def _find_normalized(content: str, search: str) -> tuple[int, int] | None:
    """
    Find the region in `content` whose whitespace-normalized form
    matches the whitespace-normalized `search`.

    Returns (start, end) of the matched region in the original content,
    or None if not found.
    """
    norm_search = _normalize_ws(search)
    # Slide a window of roughly the same length (±50%) over content.
    search_len = len(search)
    window_min = max(1, int(search_len * 0.5))
    window_max = int(search_len * 2.0) + 10
    step = max(1, search_len // 4)

    for start in range(0, len(content), step):
        for end in range(start + window_min, min(start + window_max, len(content) + 1)):
            candidate = content[start:end]
            if _normalize_ws(candidate) == norm_search:
                return start, end

    return None


def _similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio for two strings."""
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _find_fuzzy(
    content: str,
    search: str,
    threshold: float,
) -> tuple[int, int, float] | None:
    """
    Find the most similar region in `content` to `search`.

    Returns (start, end, similarity) if above threshold, else None.

    Three passes, coarse to fine, so the scan stays linear in content
    length while still being able to land on the true match:

    1. **Coarse sweep** at 1/4-search-length steps, to find roughly
       where the best region is.
    2. **Offset refinement** at single-character steps within one
       coarse step either side. A real match rarely begins exactly on a
       multiple of the step size, and a window shifted even a few
       characters off scores far below the same text aligned — which
       used to sink otherwise-perfect matches below the threshold.
    3. **Length refinement** over a range of window sizes. The matching
       region is often not the same length as the search string: a
       re-indented block, a wrapped signature, a line with a trailing
       comment. A fixed window can only ever see part of it.
    """
    search_len = len(search)
    if search_len == 0 or not content:
        return None

    best_ratio = 0.0
    best_start = -1
    best_end = -1

    def consider(start: int, end: int) -> None:
        nonlocal best_ratio, best_start, best_end
        if start < 0 or end > len(content) or end <= start:
            return
        ratio = _similarity(content[start:end], search)
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = start
            best_end = end

    # 1. Coarse sweep.
    step = max(1, search_len // 4)
    for start in range(0, max(1, len(content) - search_len // 2), step):
        consider(start, start + search_len)

    if best_start < 0:
        return None

    # 2. Refine the offset one character at a time around the winner.
    coarse_start = best_start
    for start in range(coarse_start - step, coarse_start + step + 1):
        consider(start, start + search_len)

    # 3. Refine the window length around the winner, in both directions.
    #    Capped at +/-50% so a runaway window cannot swallow unrelated
    #    code and then be "replaced".
    span = max(1, search_len // 2)
    length_step = max(1, span // 8)
    anchored_start = best_start
    for delta in range(-span, span + 1, length_step):
        consider(anchored_start, anchored_start + search_len + delta)

    if best_ratio >= threshold and best_start >= 0:
        return best_start, best_end, best_ratio

    return None


class SearchReplaceEditor:
    """
    Structured search-replace editor.

    Parameters
    ----------
    similarity_threshold:
        Minimum fuzzy-match ratio to accept (0–1).  Below this the edit
        fails rather than silently modifying the wrong region.
    patch_manager:
        Override for testing.  Production code leaves this as None
        (uses the thread-local active patch manager, which is the
        standard approval gate path).
    """

    def __init__(
        self,
        similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
        patch_manager: ChangeManager | None = None,
    ) -> None:
        self._threshold = similarity_threshold
        self._pm_override = patch_manager

    def apply(
        self,
        path: str,
        search: str,
        replace: str,
        count: int = 1,
    ) -> EditResult:
        """
        Find `search` in `path` and replace up to `count` occurrences.

        The edit is staged via ChangeManager when one is active.  If no
        ChangeManager is active (interactive/direct mode), the file is
        written directly.

        Parameters
        ----------
        path:
            Workspace-relative or absolute path.
        search:
            Text to find.
        replace:
            Text to substitute.
        count:
            Maximum occurrences to replace (-1 = all).
        """
        resolved = self._resolve(path)
        if not resolved.exists():
            return EditResult(
                succeeded=False,
                path=str(resolved),
                strategy=None,
                similarity=0.0,
                occurrences=0,
                error=f"File not found: {resolved}",
            )

        try:
            original = read_text(resolved)
        except OSError as exc:
            return EditResult(
                succeeded=False,
                path=str(resolved),
                strategy=None,
                similarity=0.0,
                occurrences=0,
                error=f"Cannot read file: {exc}",
            )

        try:
            updated, strategy, similarity, occurrences = self._apply_replacement(
                original, search, replace, count
            )
        except MatchFailed as exc:
            return EditResult(
                succeeded=False,
                path=str(resolved),
                strategy=None,
                similarity=0.0,
                occurrences=0,
                error=str(exc),
            )

        if updated == original:
            return EditResult(
                succeeded=True,
                path=str(resolved),
                strategy=strategy,
                similarity=similarity,
                occurrences=0,  # no-op
                error="",
            )

        self._write(resolved, original, updated)

        logger.info(
            "SearchReplaceEditor: %s %d replacement(s) via %s (sim=%.2f)",
            resolved,
            occurrences,
            strategy,
            similarity,
        )

        return EditResult(
            succeeded=True,
            path=str(resolved),
            strategy=strategy,
            similarity=similarity,
            occurrences=occurrences,
        )

    def validate(self, path: str, search: str) -> tuple[bool, float]:
        """
        Check whether `search` can be found in `path` without modifying.

        Returns (found, similarity_score).
        """
        resolved = self._resolve(path)
        if not resolved.exists():
            return False, 0.0
        try:
            content = read_text(resolved)
        except OSError:
            return False, 0.0

        if _find_exact(content, search) >= 0:
            return True, 1.0

        result = _find_normalized(content, search)
        if result is not None:
            return True, 0.95

        result_f = _find_fuzzy(content, search, self._threshold)
        if result_f is not None:
            return True, result_f[2]

        return False, 0.0

    # ── Internals ─────────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        """
        Resolve `path` and confirm it stays inside the workspace.

        The boundary check itself is `_ensure_within_workspace` — one
        implementation, shared with every other file tool, so there is
        no second version of the rule to keep in sync or to get subtly
        wrong. All this adds is anchoring a relative path to the
        workspace root rather than to the process's current directory,
        which is what a tool argument written by the model means.
        """

        candidate = Path(path)

        if not candidate.is_absolute():
            path = str(get_workspace_root() / candidate)

        return _ensure_within_workspace(path)

    def _apply_replacement(
        self,
        content: str,
        search: str,
        replace: str,
        count: int,
    ) -> tuple[str, MatchStrategy, float, int]:
        """
        Try matching strategies in order and return
        (updated_content, strategy, similarity, occurrences_replaced).

        Raises MatchFailed if no strategy succeeds.
        """
        if not search:
            raise MatchFailed("Search string is empty — refusing to replace.")

        # 1. Exact match
        if search in content:
            occurrences = content.count(search)
            if count == -1:
                updated = content.replace(search, replace)
                replaced = occurrences
            else:
                updated = content
                replaced = 0
                remaining = count
                pos = 0
                while remaining > 0:
                    idx = updated.find(search, pos)
                    if idx == -1:
                        break
                    updated = updated[:idx] + replace + updated[idx + len(search) :]
                    pos = idx + len(replace)
                    replaced += 1
                    remaining -= 1
            return updated, "exact", 1.0, replaced

        # 2. Whitespace-normalized match
        norm_result = _find_normalized(content, search)
        if norm_result is not None:
            start, end = norm_result
            # Replace exactly the matched region in the original content.
            updated = content[:start] + replace + content[end:]
            return updated, "normalized", 0.95, 1

        # 3. Fuzzy match
        fuzzy_result = _find_fuzzy(content, search, self._threshold)
        if fuzzy_result is not None:
            start, end, sim = fuzzy_result
            updated = content[:start] + replace + content[end:]
            return updated, "fuzzy", sim, 1

        raise MatchFailed(
            f"Search string not found in file.\n"
            f"Tried: exact, whitespace-normalized, fuzzy (threshold={self._threshold}).\n"
            f"First 80 chars of search: {search[:80]!r}"
        )

    def _write(self, path: Path, original: str, updated: str) -> None:
        """Stage or write the change."""
        pm = self._pm_override or get_active_patch_manager()
        if pm is not None:
            pm.propose(str(path), original, updated)
        else:
            write_text(path, updated)
