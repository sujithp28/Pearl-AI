"""
Tests for the stdlib `.env` loader that replaced `python-dotenv`.

Config loading is load-bearing for every provider setting, so the
replacement is pinned in detail — especially the "existing
environment variables win" rule, which CI and the end-to-end tests
depend on: they launch Pearl with an explicit `PEARL_LLM_PROVIDER`
and must not have a developer's local `.env` override it.
"""

from __future__ import annotations

import os

from src.config.settings import load_env_file


def _write(tmp_path, content: str):
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_simple_key_value_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(_write(tmp_path, "PEARL_TEST_KEY=hello\n"))

    assert os.environ["PEARL_TEST_KEY"] == "hello"


def test_existing_environment_variables_are_never_overridden(tmp_path, monkeypatch):
    """
    The rule CI relies on. If a local .env could override an
    explicitly-exported variable, the e2e tests would silently talk to
    a real model server instead of the scripted provider.
    """

    monkeypatch.setenv("PEARL_TEST_KEY", "from-environment")

    load_env_file(_write(tmp_path, "PEARL_TEST_KEY=from-dotenv\n"))

    assert os.environ["PEARL_TEST_KEY"] == "from-environment"


def test_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(
        _write(
            tmp_path,
            "# a comment\n\n   \nPEARL_TEST_KEY=value\n# trailing comment\n",
        )
    )

    assert os.environ["PEARL_TEST_KEY"] == "value"


def test_strips_export_prefix(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(_write(tmp_path, "export PEARL_TEST_KEY=exported\n"))

    assert os.environ["PEARL_TEST_KEY"] == "exported"


def test_strips_matching_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_A", raising=False)
    monkeypatch.delenv("PEARL_B", raising=False)

    load_env_file(_write(tmp_path, "PEARL_A=\"double\"\nPEARL_B='single'\n"))

    assert os.environ["PEARL_A"] == "double"
    assert os.environ["PEARL_B"] == "single"


def test_keeps_unmatched_quotes_verbatim(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(_write(tmp_path, 'PEARL_TEST_KEY="unclosed\n'))

    assert os.environ["PEARL_TEST_KEY"] == '"unclosed'


def test_values_containing_equals_are_preserved(tmp_path, monkeypatch):
    # e.g. a base URL with a query string, or a padded base64 secret.
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(_write(tmp_path, "PEARL_TEST_KEY=a=b=c\n"))

    assert os.environ["PEARL_TEST_KEY"] == "a=b=c"


def test_urls_survive_intact(tmp_path, monkeypatch):
    # The real-world case: OLLAMA_BASE_URL.
    monkeypatch.delenv("PEARL_TEST_URL", raising=False)

    load_env_file(_write(tmp_path, "PEARL_TEST_URL=http://localhost:11434/v1\n"))

    assert os.environ["PEARL_TEST_URL"] == "http://localhost:11434/v1"


def test_lines_without_an_equals_sign_are_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(_write(tmp_path, "NOT_A_PAIR\nPEARL_TEST_KEY=ok\n"))

    assert os.environ["PEARL_TEST_KEY"] == "ok"
    assert "NOT_A_PAIR" not in os.environ


def test_empty_values_are_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(_write(tmp_path, "PEARL_TEST_KEY=\n"))

    assert os.environ["PEARL_TEST_KEY"] == ""


def test_missing_file_is_not_an_error(tmp_path):
    # Pearl must start fine with no .env at all — every setting just
    # falls back to its documented default.
    load_env_file(tmp_path / "does-not-exist.env")


def test_directory_instead_of_file_is_not_an_error(tmp_path):
    load_env_file(tmp_path)


def test_whitespace_around_keys_and_values_is_trimmed(tmp_path, monkeypatch):
    monkeypatch.delenv("PEARL_TEST_KEY", raising=False)

    load_env_file(_write(tmp_path, "  PEARL_TEST_KEY  =  spaced  \n"))

    assert os.environ["PEARL_TEST_KEY"] == "spaced"
