import json
import time
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from src.llm.client import LLMClient


def _make_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _make_connection_error() -> APIConnectionError:
    request = httpx.Request("POST", "http://test")
    return APIConnectionError(request=request)


def test_generate_retries_transient_errors_then_succeeds(monkeypatch):
    client = LLMClient()

    calls = {"count": 0}

    def flaky_create(**kwargs):
        calls["count"] += 1

        if calls["count"] < 3:
            raise _make_connection_error()

        return _make_response("hello")

    monkeypatch.setattr(client.client.chat.completions, "create", flaky_create)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    result = client.generate("hi")

    assert result == "hello"
    assert calls["count"] == 3


def test_generate_gives_up_after_max_retries(monkeypatch):
    client = LLMClient()

    def always_fails(**kwargs):
        raise _make_connection_error()

    monkeypatch.setattr(client.client.chat.completions, "create", always_fails)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(APIConnectionError):
        client.generate("hi")


def test_generate_does_not_retry_non_transient_errors(monkeypatch):
    client = LLMClient()

    calls = {"count": 0}

    def raises_value_error(**kwargs):
        calls["count"] += 1
        raise ValueError("boom")

    monkeypatch.setattr(client.client.chat.completions, "create", raises_value_error)

    with pytest.raises(ValueError):
        client.generate("hi")

    assert calls["count"] == 1


def test_extract_json_avoids_greedy_match_across_trailing_prose():
    client = LLMClient()

    text = (
        "Here is the result:\n"
        '{"tool": "read_file", "arguments": {"path": "a.txt"}}\n'
        'Note: this uses the "{}" pattern for objects.'
    )

    cleaned = client._extract_json(text)

    assert cleaned == '{"tool": "read_file", "arguments": {"path": "a.txt"}}'
    assert json.loads(cleaned) == {
        "tool": "read_file",
        "arguments": {"path": "a.txt"},
    }
