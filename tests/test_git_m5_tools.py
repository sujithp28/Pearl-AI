"""
Tests for M5 git intelligence tools — git_stage and git_blame
(src/tools/git_tools.py).

Coverage
--------
git_stage:
  * raises GitError outside a git repo
  * stages all files when files is None/empty
  * stages specific files
  * raises GitError when git add fails

git_blame:
  * raises GitError outside a git repo
  * raises ValueError on empty path
  * raises ValueError when start_line < 1
  * raises ValueError when end_line < start_line
  * returns list[dict] with expected keys for a real file
  * porcelain parser handles multi-line blame output
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.git_tools import (
    GitError,
    _parse_blame_porcelain,
    git_blame,
    git_stage,
)

# ---------------------------------------------------------------------------
# git_stage
# ---------------------------------------------------------------------------


class TestGitStage:
    def test_raises_outside_git_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        # tmp_path is not a git repo
        with pytest.raises(GitError, match="Not a git repository"):
            git_stage()

    def test_stages_all_when_files_is_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        run_results = [
            # _ensure_git_repo: rev-parse
            MagicMock(returncode=0, stdout="true\n", stderr=""),
            # git add .
            MagicMock(returncode=0, stdout="", stderr=""),
            # git_status → rev-parse
            MagicMock(returncode=0, stdout="true\n", stderr=""),
            # git status --porcelain=v1 --branch
            MagicMock(returncode=0, stdout="## main\nA  a.py\n", stderr=""),
        ]
        with patch("src.tools.git_tools.subprocess.run", side_effect=run_results):
            result = git_stage()
        assert "Staged" in result

    def test_stages_specific_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        run_results = [
            MagicMock(returncode=0, stdout="true\n", stderr=""),  # _ensure_git_repo
            MagicMock(returncode=0, stdout="", stderr=""),         # git add -- a.py
            MagicMock(returncode=0, stdout="true\n", stderr=""),  # _ensure_git_repo in git_status
            MagicMock(returncode=0, stdout="## main\nA  a.py\n", stderr=""),
        ]
        with patch("src.tools.git_tools.subprocess.run", side_effect=run_results):
            result = git_stage(["a.py"])
        assert "Staged" in result

    def test_raises_on_git_add_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        run_results = [
            MagicMock(returncode=0, stdout="true\n", stderr=""),  # _ensure_git_repo
            MagicMock(returncode=128, stdout="", stderr="error: pathspec 'x.py' did not match"),
        ]
        with patch("src.tools.git_tools.subprocess.run", side_effect=run_results):
            with pytest.raises(GitError, match="git add failed"):
                git_stage(["x.py"])

    def test_raises_on_empty_string_in_files_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        run_results = [
            MagicMock(returncode=0, stdout="true\n", stderr=""),  # _ensure_git_repo
        ]
        with patch("src.tools.git_tools.subprocess.run", side_effect=run_results):
            with pytest.raises(ValueError):
                git_stage([""])


# ---------------------------------------------------------------------------
# git_blame
# ---------------------------------------------------------------------------


class TestGitBlame:
    def test_raises_outside_git_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(GitError, match="Not a git repository"):
            git_blame("a.py")

    def test_raises_on_empty_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch(
            "src.tools.git_tools.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="true\n", stderr=""),
        ):
            with pytest.raises(ValueError, match="'path' must not be empty"):
                git_blame("")

    def test_raises_on_start_line_less_than_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch(
            "src.tools.git_tools.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="true\n", stderr=""),
        ):
            with pytest.raises(ValueError, match="start_line"):
                git_blame("a.py", start_line=0)

    def test_raises_when_end_line_lt_start_line(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        run_results = [
            MagicMock(returncode=0, stdout="true\n", stderr=""),  # _ensure_git_repo
        ]
        with patch("src.tools.git_tools.subprocess.run", side_effect=run_results):
            with pytest.raises(ValueError, match="end_line"):
                git_blame("a.py", start_line=5, end_line=2)

    def test_raises_on_git_blame_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        run_results = [
            MagicMock(returncode=0, stdout="true\n", stderr=""),  # _ensure_git_repo
            MagicMock(returncode=128, stdout="", stderr="fatal: no such path"),
        ]
        with patch("src.tools.git_tools.subprocess.run", side_effect=run_results):
            with pytest.raises(GitError, match="git blame failed"):
                git_blame("missing.py")

    def test_returns_list_of_dicts_with_expected_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        porcelain = (
            "abc123def456789012345678901234567890abcd 1 1 1\n"
            "author Alice\n"
            "author-mail <alice@example.com>\n"
            "author-time 1700000000\n"
            "author-tz +0000\n"
            "committer Alice\n"
            "committer-mail <alice@example.com>\n"
            "committer-time 1700000000\n"
            "committer-tz +0000\n"
            "summary Initial commit\n"
            "filename a.py\n"
            "\tdef hello(): pass\n"
        )
        monkeypatch.chdir(tmp_path)
        run_results = [
            MagicMock(returncode=0, stdout="true\n", stderr=""),  # _ensure_git_repo
            MagicMock(returncode=0, stdout=porcelain, stderr=""),  # git blame
        ]
        with patch("src.tools.git_tools.subprocess.run", side_effect=run_results):
            records = git_blame("a.py")

        assert isinstance(records, list)
        assert len(records) == 1
        rec = records[0]
        assert "commit" in rec
        assert "author" in rec
        assert "date" in rec
        assert "line_number" in rec
        assert "content" in rec
        assert rec["author"] == "Alice"
        assert rec["content"] == "def hello(): pass"


# ---------------------------------------------------------------------------
# _parse_blame_porcelain (unit)
# ---------------------------------------------------------------------------


class TestParseBlamePorcelain:
    def test_empty_output_returns_empty_list(self) -> None:
        assert _parse_blame_porcelain("") == []

    def test_parses_multiple_lines(self) -> None:
        porcelain = (
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 2\n"
            "author Alice\n"
            "author-time 1700000000\n"
            "\tline one\n"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 2 2\n"
            "author Alice\n"
            "author-time 1700000000\n"
            "\tline two\n"
        )
        records = _parse_blame_porcelain(porcelain)
        assert len(records) == 2
        assert records[0]["content"] == "line one"
        assert records[1]["content"] == "line two"
