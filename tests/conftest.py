"""
Shared pytest fixtures for Pearl's test suite.
"""

from __future__ import annotations

import os

import pytest

# openai SDK v2+ validates credentials at constructor time.  Tests that mock
# the LLM client never reach the real API, so a sentinel value is sufficient
# and must be present before any test module imports trigger provider creation.
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True)
def _isolated_pearl_home(tmp_path_factory, monkeypatch):
    """
    Redirect `$HOME` (and therefore `Path.home()`) to a throwaway
    directory for every test, including real subprocesses.

    `CheckpointManager` (and anything else that ever reads `~/.pearl`)
    defaults to the real user's home when not given an explicit
    `pearl_home`. Without this, a test exercising the default —
    directly, or indirectly via `AutonomousExecutor`'s own default
    `CheckpointManager()` — writes real checkpoint state into a real
    developer's home directory. That happened once already: a full
    suite run left real `shadow.git` stores under
    `~/.pearl/workspaces/` on the machine this was developed on.

    Sets the `HOME` environment variable rather than monkeypatching
    `Path.home` directly, and deliberately for two reasons:

    - `Path.home()` reads `$HOME` internally on POSIX, so this covers
      it without a separate patch.
    - `tests/test_e2e_mcp.py` spawns real subprocesses via
      `env=os.environ.copy()`; a `Path.home` monkeypatch only affects
      this process and would never reach them, but an environment
      variable set before the subprocess is spawned is inherited
      automatically.

    Uses `tmp_path_factory` (a directory of its own) rather than the
    per-test `tmp_path` — several tests already use `tmp_path` itself
    as their workspace and list its contents; nesting a fake-home
    directory inside it would show up as an unexpected extra file.

    Autouse and session-wide rather than fixed per test file, so this
    protects every current call site and any future one without
    relying on each test remembering to inject `pearl_home=`.
    """

    fake_home = tmp_path_factory.mktemp("pearl_home")

    monkeypatch.setenv("HOME", str(fake_home))


@pytest.fixture(autouse=True)
def _clean_workspace_root():
    """
    Clear the thread-local workspace root before and after every test.

    Same class of bug as `_isolated_pearl_home` above, on a different
    global — and it has already caused real damage.

    Tools resolve paths against `get_workspace_root()`, which falls back
    to the current directory *only when nothing has been set*. Tests
    written against that fallback use `monkeypatch.chdir(tmp_path)` and
    assume it applies. It does not, once any earlier test in the process
    has set the thread-local: the value survives, `chdir` is ignored, and
    the tool operates on whatever workspace that test chose.

    In practice that meant `tests/test_refactor_tools.py` — which calls
    `rename_symbol` — ran against this repository instead of its own
    `tmp_path`, renaming a symbol across 13 real source files. The tests
    still passed, because they only assert on files inside `tmp_path`.
    Nothing failed; the damage was only visible in `git status`.

    Clearing before each test restores the fallback these tests rely on.
    Clearing after keeps one test's explicit `set_workspace_root` from
    reaching the next.
    """
    from src.config.workspace import clear_workspace_root

    clear_workspace_root()
    yield
    clear_workspace_root()


def write_lf(path, text: str) -> None:
    """
    Write `text` byte-for-byte, with no newline translation.

    ``Path.write_text`` translates ``\n`` to ``\r\n`` on Windows, so a
    fixture that says LF lands as CRLF there. Pearl's editing tools now
    preserve whatever endings a file really has, and its approval gate
    compares staged content against the file's real bytes — so a
    platform-translated fixture no longer matches the literal a test
    stages beside it. Use this whenever a test's assertions name the
    exact bytes of a file.
    """

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
