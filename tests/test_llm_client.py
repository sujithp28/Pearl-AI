import json
import time
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from src.config.settings import Settings
from src.llm.client import LLMClient
from src.llm.providers.openai_compatible import OpenAICompatibleProvider


@pytest.fixture(autouse=True)
def _force_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force all LLMClient() calls in this module to use OpenAICompatibleProvider."""
    monkeypatch.setattr(Settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(Settings, "OPENAI_API_KEY", "dummy-test-key")


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

    monkeypatch.setattr(client.provider.client.chat.completions, "create", flaky_create)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    result = client.generate("hi")

    assert result == "hello"
    assert calls["count"] == 3


def test_generate_gives_up_after_max_retries(monkeypatch):
    client = LLMClient()

    def always_fails(**kwargs):
        raise _make_connection_error()

    monkeypatch.setattr(client.provider.client.chat.completions, "create", always_fails)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(APIConnectionError):
        client.generate("hi")


def test_generate_does_not_retry_non_transient_errors(monkeypatch):
    client = LLMClient()

    calls = {"count": 0}

    def raises_value_error(**kwargs):
        calls["count"] += 1
        raise ValueError("boom")

    monkeypatch.setattr(
        client.provider.client.chat.completions, "create", raises_value_error
    )

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


# ---------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------


def _capture_messages(monkeypatch, client: LLMClient) -> list[list[dict]]:
    """
    Record the `messages` array sent to the provider on each call.
    """

    captured: list[list[dict]] = []

    def fake_create(**kwargs):
        captured.append(kwargs["messages"])
        return _make_response("ok")

    monkeypatch.setattr(client.provider.client.chat.completions, "create", fake_create)

    return captured


def test_generate_sends_only_the_prompt_when_no_history_is_given(monkeypatch):
    client = LLMClient()
    captured = _capture_messages(monkeypatch, client)

    client.generate("hi")

    assert captured[0] == [{"role": "user", "content": "hi"}]


def test_generate_sends_history_before_the_prompt(monkeypatch):
    client = LLMClient()
    captured = _capture_messages(monkeypatch, client)

    client.generate(
        "and now?",
        history=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
        ],
    )

    assert captured[0] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "and now?"},
    ]


def test_generate_json_never_sends_history(monkeypatch):
    """
    Planning and replanning go through `generate_json`, and must stay
    stateless — a plan that silently depended on unrelated chat
    history would be neither reproducible nor debuggable.
    """

    client = LLMClient()

    captured: list[list[dict]] = []

    def fake_create(**kwargs):
        captured.append(kwargs["messages"])
        return _make_response('{"steps": []}')

    monkeypatch.setattr(client.provider.client.chat.completions, "create", fake_create)

    client.generate_json("plan something")

    assert captured[0] == [{"role": "user", "content": "plan something"}]


# ---------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------


def test_generate_raises_immediately_if_already_cancelled(monkeypatch):
    client = LLMClient()

    called = {"n": 0}

    def fake_create(**kwargs):
        called["n"] += 1
        return _make_response("should not be reached")

    monkeypatch.setattr(client.provider.client.chat.completions, "create", fake_create)

    from src.llm.client import LLMCancelled

    with pytest.raises(LLMCancelled):
        client.generate("hi", cancel_check=lambda: True)

    # The request must never have been sent.
    assert called["n"] == 0


def test_generate_proceeds_normally_when_cancel_check_returns_false(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(
        client.provider.client.chat.completions,
        "create",
        lambda **kwargs: _make_response("ok"),
    )

    result = client.generate("hi", cancel_check=lambda: False)

    assert result == "ok"


def test_generate_with_no_cancel_check_behaves_exactly_as_before(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(
        client.provider.client.chat.completions,
        "create",
        lambda **kwargs: _make_response("ok"),
    )

    assert client.generate("hi") == "ok"


def test_retry_backoff_is_interrupted_by_cancellation(monkeypatch):
    """
    Regression: a plain `time.sleep(delay)` retry wait cannot be
    interrupted, so cancelling during a multi-second backoff had no
    effect until the full delay elapsed. The wait must now poll and
    raise LLMCancelled promptly instead of blocking for the full delay.
    """

    from src.llm.client import LLMCancelled

    client = LLMClient()

    calls = {"n": 0}

    def flaky_create(**kwargs):
        calls["n"] += 1
        raise _make_connection_error()

    monkeypatch.setattr(client.provider.client.chat.completions, "create", flaky_create)

    # Cancelled starting from the second check onward, so the first
    # attempt is sent (proving cancellation doesn't block a request
    # already decided upon) and the retry backoff after it is cut short.
    checks = {"n": 0}

    def cancel_after_first_check():
        checks["n"] += 1
        return checks["n"] > 1

    start = time.time()

    with pytest.raises(LLMCancelled):
        client.generate("hi", cancel_check=cancel_after_first_check)

    elapsed = time.time() - start

    assert calls["n"] == 1  # only the first attempt was sent
    # The base retry delay is 1.0s; an interrupted wait must return
    # in a small fraction of that, not block for the full delay.
    assert elapsed < 0.5


def test_interruptible_sleep_without_a_cancel_check_behaves_like_time_sleep(
    monkeypatch,
):
    from src.llm.client import _interruptible_sleep

    slept = {"seconds": None}
    monkeypatch.setattr(time, "sleep", lambda s: slept.__setitem__("seconds", s))

    _interruptible_sleep(0.3, cancel_check=None)

    assert slept["seconds"] == 0.3
