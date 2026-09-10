"""
Tests for inline completion (`src/agent/completion.py`) and its two
transports (`pearl/complete` over MCP, `POST /api/complete` over HTTP).

Completion sits on the typing path, which drives most of what is
asserted here: it must never raise into the editor, must not re-run
inference for repeated context, and must decline cheaply in contexts
where a suggestion would be noise.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.completion import CompletionService


class _FakeClient:
    """Records calls and returns a canned continuation."""

    def __init__(self, text: str = " a + b") -> None:
        self.text = text
        self.calls: list[dict] = []

    def complete_raw(self, prompt, temperature, max_new_tokens, stop=None):
        self.calls.append(
            {
                "prompt": prompt,
                "temperature": temperature,
                "max_new_tokens": max_new_tokens,
                "stop": stop,
            }
        )
        return self.text


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


class TestCompletion:
    def test_returns_the_model_continuation(self):
        svc = CompletionService(client=_FakeClient(" a + b"))
        result = svc.complete("def add(a, b):\n    return", language="python")
        assert result.text == " a + b"
        assert result.cached is False

    def test_uses_the_raw_path_not_chat(self):
        """
        The whole reason completion works: complete_raw() bypasses the
        chat template. Calling generate() here would reintroduce the
        refusal bug this feature was built to fix.
        """
        client = _FakeClient()
        svc = CompletionService(client=client)
        svc.complete("def add(a, b):\n    return")
        assert client.calls, "no model call was made"
        assert not hasattr(client, "generate_called")

    def test_prompt_is_the_prefix_verbatim(self):
        client = _FakeClient()
        svc = CompletionService(client=client)
        svc.complete("def add(a, b):\n    return")
        assert client.calls[0]["prompt"] == "def add(a, b):\n    return"

    def test_temperature_is_zero_for_determinism(self):
        """
        The same context must suggest the same thing — a completion that
        flickers between alternatives on identical input is unusable.
        """
        client = _FakeClient()
        CompletionService(client=client).complete("x =")
        assert client.calls[0]["temperature"] == 0.0


# ---------------------------------------------------------------------------
# Pre-inference gates
# ---------------------------------------------------------------------------


class TestGates:
    @pytest.mark.parametrize("prefix", ["", "   ", "\n", "\t\n  "])
    def test_declines_empty_context_without_calling_the_model(self, prefix):
        client = _FakeClient()
        result = CompletionService(client=client).complete(prefix)
        assert result.is_empty
        assert result.declined_reason == "trivial-context"
        assert client.calls == [], "must not spend inference on empty context"

    def test_declines_after_a_blank_line(self):
        client = _FakeClient()
        result = CompletionService(client=client).complete("x = 1\n\n")
        assert result.is_empty
        assert client.calls == []


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCache:
    def test_repeat_context_is_served_from_cache(self):
        """Completion fires per keystroke; identical context must not re-infer."""
        client = _FakeClient()
        svc = CompletionService(client=client)

        first = svc.complete("def add(a, b):\n    return")
        second = svc.complete("def add(a, b):\n    return")

        assert first.text == second.text
        assert second.cached is True
        assert len(client.calls) == 1, "cached request re-ran inference"

    def test_different_context_is_not_cached_together(self):
        client = _FakeClient()
        svc = CompletionService(client=client)
        svc.complete("def add(a, b):\n    return")
        svc.complete("def sub(a, b):\n    return")
        assert len(client.calls) == 2

    def test_suffix_participates_in_the_cache_key(self):
        """Same prefix with different following text is a different request."""
        client = _FakeClient()
        svc = CompletionService(client=client)
        svc.complete("x =", suffix="\nprint(x)")
        svc.complete("x =", suffix="\nreturn x")
        assert len(client.calls) == 2

    def test_clear_cache_forces_reinference(self):
        client = _FakeClient()
        svc = CompletionService(client=client)
        svc.complete("x =")
        svc.clear_cache()
        svc.complete("x =")
        assert len(client.calls) == 2

    def test_empty_completions_are_not_cached(self):
        """A failed completion must not poison the cache for that context."""
        client = _FakeClient("")
        svc = CompletionService(client=client)
        svc.complete("x =")
        svc.complete("x =")
        assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


class TestPostProcessing:
    def test_strips_markdown_fences(self):
        """Small models emit fences even on the raw path, having seen them constantly."""
        svc = CompletionService(client=_FakeClient("```python\n a + b\n```"))
        assert "```" not in svc.complete("def add(a, b):\n    return").text

    def test_strips_repeated_prefix(self):
        """
        Models frequently restate the end of the prompt before continuing
        it; inserting that would duplicate what the developer just typed.
        """
        svc = CompletionService(client=_FakeClient("    return a + b"))
        result = svc.complete("def add(a, b):\n    return")
        assert not result.text.startswith("    return")

    def test_strips_overlap_with_suffix(self):
        """
        Without FIM the model cannot see past the cursor, so it happily
        regenerates the line that already follows it.
        """
        svc = CompletionService(client=_FakeClient(" x\n    return total"))
        result = svc.complete("total =", suffix="\n    return total")
        assert not result.text.endswith("\n    return total")

    def test_strips_trailing_whitespace(self):
        svc = CompletionService(client=_FakeClient(" a + b   \n  "))
        assert svc.complete("def add(a, b):\n    return").text == " a + b"

    def test_whitespace_only_completion_is_declined(self):
        svc = CompletionService(client=_FakeClient("   \n  "))
        result = svc.complete("x =")
        assert result.is_empty
        assert result.declined_reason == "empty-after-postprocessing"


# ---------------------------------------------------------------------------
# Failure handling — must never raise into the editor
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_model_failure_returns_empty_not_an_exception(self):
        client = MagicMock()
        client.complete_raw.side_effect = RuntimeError("model exploded")
        result = CompletionService(client=client).complete("x =")
        assert result.is_empty
        assert "model exploded" in (result.declined_reason or "")

    def test_context_length_error_is_also_swallowed(self):
        from src.llm.errors import ContextLengthError

        client = MagicMock()
        client.complete_raw.side_effect = ContextLengthError("too long")
        result = CompletionService(client=client).complete("x =")
        assert result.is_empty


# ---------------------------------------------------------------------------
# Language-aware stop sequences
# ---------------------------------------------------------------------------


class TestStopSequences:
    def test_python_stops_include_def_and_class(self):
        client = _FakeClient()
        CompletionService(client=client).complete("x =", language="python")
        stop = client.calls[0]["stop"]
        assert "\ndef " in stop and "\nclass " in stop

    def test_language_specific_stops_are_added(self):
        client = _FakeClient()
        CompletionService(client=client).complete("x =", language="rust")
        assert "\nfn " in client.calls[0]["stop"]

    def test_unknown_language_still_gets_defaults(self):
        client = _FakeClient()
        CompletionService(client=client).complete("x =", language="cobol")
        assert "\n\n" in client.calls[0]["stop"]


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class TestMCPTransport:
    def _server(self):
        from src.agent.dispatcher import ToolDispatcher
        from src.main import build_registry
        from src.mcp.server import MCPServer

        registry = build_registry()
        return MCPServer(
            registry=registry, dispatcher=ToolDispatcher(registry), planner=None
        )

    def test_returns_a_completion(self):
        server = self._server()
        server._completion_service = CompletionService(client=_FakeClient(" a + b"))

        result = server._complete(
            {"prefix": "def add(a, b):\n    return", "language": "python"},
            lambda *a, **k: None,
        )

        assert result["completion"] == " a + b"
        assert result["cached"] is False
        assert result["declinedReason"] is None

    def test_missing_prefix_is_a_protocol_error(self):
        from src.mcp.protocol import MCPProtocolError

        with pytest.raises(MCPProtocolError):
            self._server()._complete({}, lambda *a, **k: None)

    def test_non_string_suffix_is_rejected(self):
        from src.mcp.protocol import MCPProtocolError

        with pytest.raises(MCPProtocolError):
            self._server()._complete(
                {"prefix": "x", "suffix": 42}, lambda *a, **k: None
            )

    def test_model_failure_is_not_a_protocol_error(self):
        """
        A broken model must yield an empty completion, not a JSON-RPC
        error — the editor should silently show nothing, not surface a
        protocol failure while the developer is typing.
        """
        server = self._server()
        client = MagicMock()
        client.complete_raw.side_effect = RuntimeError("down")
        server._completion_service = CompletionService(client=client)

        result = server._complete({"prefix": "x ="}, lambda *a, **k: None)
        assert result["completion"] == ""


class TestHTTPTransport:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from src.api import server as srv

        srv.init_session(tmp_path)
        srv._completion_service = CompletionService(client=_FakeClient(" a + b"))
        return TestClient(srv.app)

    def test_returns_a_completion(self, client):
        response = client.post(
            "/api/complete",
            json={"prefix": "def add(a, b):\n    return", "language": "python"},
        )
        assert response.status_code == 200
        assert response.json()["completion"] == " a + b"

    def test_missing_prefix_is_a_validation_error(self, client):
        assert client.post("/api/complete", json={}).status_code == 422

    def test_declined_context_returns_200_with_a_reason(self, client):
        """
        Not an error status: "nothing to suggest here" is a normal
        outcome a keystroke-driven caller must not have to catch.
        """
        response = client.post("/api/complete", json={"prefix": ""})
        assert response.status_code == 200
        body = response.json()
        assert body["completion"] == ""
        assert body["declined_reason"] == "trivial-context"
