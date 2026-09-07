"""
Guards that a test cannot modify the real repository.

This is a regression suite for damage that already happened twice:

* `~/.pearl` — a full suite run wrote real checkpoint stores into a
  developer's home directory (see `_isolated_pearl_home` in conftest).
* The repository itself — `tests/test_refactor_tools.py` called
  `rename_symbol` while the thread-local workspace root pointed at this
  checkout instead of its `tmp_path`, renaming a symbol across 13 real
  source files. Every test still passed, because they only assert on
  files under `tmp_path`. The damage showed up in `git status`, not in
  the suite.

The failure mode is what makes these worth testing: a test that
corrupts the repo and still reports success is invisible until someone
reads a diff.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestWorkspaceRootIsolation:
    def test_chdir_actually_confines_the_workspace_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """
        The property the write-tool tests depend on.

        They confine themselves with `monkeypatch.chdir(tmp_path)` and
        assume tools follow. That only holds while the thread-local is
        unset — which is exactly what leaked before. Asserting the
        fallback is *not* the repo would be wrong, since pytest runs from
        the repo and an unset root legitimately resolves to cwd; what
        matters is that chdir moves it.
        """
        from src.config.workspace import get_workspace_root

        monkeypatch.chdir(tmp_path)

        assert get_workspace_root() == tmp_path.resolve(), (
            "chdir did not move the workspace root — a leaked thread-local "
            "is overriding it, so write tools will escape tmp_path"
        )

    def test_thread_local_starts_clear(self):
        """
        The cwd fallback only applies when nothing is set. Tests that
        use monkeypatch.chdir rely on that, so each test must start
        with the thread-local clear.
        """
        from src.config import workspace as workspace_state

        assert not hasattr(workspace_state._local, "root"), (
            "a previous test leaked its workspace root into this one"
        )

    def test_leaked_root_does_not_survive_into_the_next_test(self, tmp_path):
        """
        Sets a root deliberately; the autouse fixture must clear it
        before the next test sees it. Paired with the test below.
        """
        from src.config.workspace import set_workspace_root

        set_workspace_root(tmp_path)

    def test_previous_tests_root_was_cleared(self):
        from src.config import workspace as workspace_state

        assert not hasattr(workspace_state._local, "root"), (
            "the root set by the previous test survived into this one"
        )


class TestRenameCannotEscapeToTheRepo:
    def test_rename_symbol_stays_inside_tmp_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """
        The exact escape that happened: rename_symbol resolves against
        get_workspace_root(), not cwd, so chdir alone did not confine it.
        """
        from src.tools.refactor_tools import rename_symbol

        monkeypatch.chdir(tmp_path)
        (tmp_path / "sample.py").write_text("class Foo: pass\nf = Foo()\n")

        # A name that genuinely appears throughout this repository — if
        # the rename escapes, it rewrites real files.
        rename_symbol("Foo", "RenamedFoo")

        assert "RenamedFoo" in (tmp_path / "sample.py").read_text()
        # And the repo is untouched.
        assert not (REPO_ROOT / "sample.py").exists()

    def test_repo_source_is_unchanged_after_a_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """
        Reads a real source file before and after, to catch an escape
        directly rather than inferring it.
        """
        from src.tools.refactor_tools import rename_symbol

        canary = REPO_ROOT / "src" / "repository" / "parsers" / "ruby_parser.py"
        before = canary.read_text(encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        (tmp_path / "sample.py").write_text("class Foo: pass\n")
        rename_symbol("Foo", "RenamedFoo")

        assert canary.read_text(encoding="utf-8") == before, (
            "a rename in a test modified real repository source"
        )
