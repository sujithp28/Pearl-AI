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
