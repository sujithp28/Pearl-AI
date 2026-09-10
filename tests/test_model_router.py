"""
Tests for M6 ModelRouter (src/llm/router.py).

Coverage
--------
* client_for — returns LLMClient; same object on second call (cache)
* planning_client / chat_client — delegates to client_for
* all_same_provider — True when no routing config set
* all_same_provider — False when different providers configured
* model override — provider.model is set when PLANNING_MODEL is configured
* fallback — when PLANNING_PROVIDER unset, falls back to LLM_PROVIDER
* MCPServer — accepts chat_llm and uses it in _chat() instead of llm
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.llm.router import ModelRouter, _effective_provider, _override_model

# ---------------------------------------------------------------------------
# _effective_provider
# ---------------------------------------------------------------------------


class TestEffectiveProvider:
    def test_returns_global_provider_when_no_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        assert _effective_provider("planning") == "openai"

    def test_returns_planning_provider_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "openai")
        assert _effective_provider("planning") == "openai"

    def test_returns_chat_provider_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "gemini")
        assert _effective_provider("chat") == "gemini"

    def test_unknown_task_falls_back_to_global(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        assert _effective_provider("embedding") == "openai"


# ---------------------------------------------------------------------------
# _override_model
# ---------------------------------------------------------------------------


class TestOverrideModel:
    def test_returns_none_when_no_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        assert _override_model("openai", "planning") is None
        assert _override_model("openai", "chat") is None

    def test_returns_planning_model_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "llama3:70b")
        assert _override_model("openai", "planning") == "llama3:70b"

    def test_returns_chat_model_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "gpt-4o-mini")
        assert _override_model("openai", "chat") == "gpt-4o-mini"

    # Pearl-specific: each task routes to its own model tier so that
    # planning uses a more capable model than chat.
    def test_pearl_chat_routes_to_chat_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_CHAT_MODEL",
            "anthropic/claude-haiku-test",
        )
        assert _override_model("pearl", "chat") == "anthropic/claude-haiku-test"

    def test_pearl_planning_routes_to_plan_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_PLAN_MODEL",
            "anthropic/claude-sonnet-test",
        )
        assert _override_model("pearl", "planning") == "anthropic/claude-sonnet-test"

    def test_global_planning_model_overrides_pearl_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PLANNING_MODEL wins over the pearl-specific tier when explicitly set.
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "override-model")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        assert _override_model("pearl", "planning") == "override-model"


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------


class TestModelRouter:
    def test_client_for_returns_llm_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")

        fake_llm = MagicMock()
        with patch("src.llm.router.LLMClient", return_value=fake_llm):
            router = ModelRouter()
            client = router.client_for("planning")

        assert client is fake_llm

    def test_client_for_caches_on_second_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")

        with patch("src.llm.router.LLMClient", return_value=MagicMock()) as mock_cls:
            router = ModelRouter()
            c1 = router.client_for("planning")
            c2 = router.client_for("planning")

        assert c1 is c2
        assert mock_cls.call_count == 1

    def test_planning_client_delegates_to_client_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")

        fake = MagicMock()
        with patch("src.llm.router.LLMClient", return_value=fake):
            router = ModelRouter()
            assert router.planning_client() is fake

    def test_chat_client_delegates_to_client_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")

        fake = MagicMock()
        with patch("src.llm.router.LLMClient", return_value=fake):
            router = ModelRouter()
            assert router.chat_client() is fake

    def test_all_same_provider_true_when_no_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        assert ModelRouter().all_same_provider() is True

    def test_all_same_provider_false_when_different_providers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "openai")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "openrouter")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        assert ModelRouter().all_same_provider() is False

    def test_all_same_provider_false_when_model_overrides_differ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "llama3:70b")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        assert ModelRouter().all_same_provider() is False

    def test_all_same_provider_false_when_pearl_has_different_model_tiers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pearl's default uses Haiku for chat and Sonnet for planning —
        # these are different models so two separate clients must be built.
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "pearl")
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_CHAT_MODEL", "haiku"
        )
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_PLAN_MODEL", "sonnet"
        )
        assert ModelRouter().all_same_provider() is False

    def test_all_same_provider_true_when_pearl_models_are_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "pearl")
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_CHAT_MODEL", "same-model"
        )
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_PLAN_MODEL", "same-model"
        )
        assert ModelRouter().all_same_provider() is True


# ---------------------------------------------------------------------------
# MCPServer.chat_llm integration
# ---------------------------------------------------------------------------


class TestMCPServerChatLlm:
    def test_chat_uses_chat_llm_when_provided(self) -> None:
        from src.mcp.protocol import JsonRpcRequest
        from src.mcp.server import MCPServer
        from src.tools.registry import ToolRegistry

        chat_chunks = ["Hello", " world"]
        chat_llm = MagicMock()
        chat_llm.generate_stream.return_value = iter(chat_chunks)

        planning_llm = MagicMock()

        registry = ToolRegistry()
        server = MCPServer(registry, llm=planning_llm, chat_llm=chat_llm)

        collected: list[str] = []

        def notify(method: str, params: dict) -> None:
            if method == "pearl/chatChunk":
                collected.append(params["chunk"])

        request = JsonRpcRequest(id=1, method="pearl/chat", params={"message": "hi"})
        response = server.handle_request(request, notify=notify)

        # The chat LLM was used, not the planning LLM
        chat_llm.generate_stream.assert_called_once()
        planning_llm.generate_stream.assert_not_called()
        assert response is not None
        assert response.result["message"] == "Hello world"
        assert collected == ["Hello", " world"]

    def test_chat_falls_back_to_llm_when_no_chat_llm(self) -> None:
        from src.mcp.protocol import JsonRpcRequest
        from src.mcp.server import MCPServer
        from src.tools.registry import ToolRegistry

        chunks = ["ok"]
        llm = MagicMock()
        llm.generate_stream.return_value = iter(chunks)

        registry = ToolRegistry()
        server = MCPServer(registry, llm=llm)  # no chat_llm

        request = JsonRpcRequest(id=1, method="pearl/chat", params={"message": "hi"})
        response = server.handle_request(request)

        llm.generate_stream.assert_called_once()
        assert response.result["message"] == "ok"
