"""
Tests for the model profile system and multi-local-model routing.

Covers the Phase-1 hybrid router additions:

* Profile resolution (auto / local / hybrid / cloud) and its precedence
  against an explicitly chosen global provider.
* The new roles: autocomplete, vision, reflection.
* Autocomplete's latency rule — local by default, never silently remote.
* Multi-model local cache: distinct GGUFs must load independently, which
  a single global singleton previously made impossible.
* Local fallback when a remote provider cannot be constructed.
* describe() honesty — it must report the GGUF actually running rather
  than the gateway model name, which does not apply on-device.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.llm.router import (
    ModelRouter,
    _effective_provider,
    _is_local_pearl,
    _local_model_file,
    _override_model,
    resolve_profile,
)


def _clear_role_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank every explicit per-role setting so profiles are what decide."""
    for name in (
        "PLANNING_PROVIDER", "CHAT_PROVIDER", "EDIT_PROVIDER",
        "CONDENSER_PROVIDER", "AUTOCOMPLETE_PROVIDER", "VISION_PROVIDER",
        "REFLECTION_PROVIDER", "PLANNING_MODEL", "CHAT_MODEL", "EDIT_MODEL",
        "CONDENSER_MODEL_NAME", "AUTOCOMPLETE_MODEL", "VISION_MODEL",
        "REFLECTION_MODEL",
    ):
        monkeypatch.setattr(f"src.llm.router.Settings.{name}", "")


def _no_remote_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
        "GEMINI_API_KEY", "PEARL_INFERENCE_API_KEY",
    ):
        monkeypatch.setattr(f"src.llm.router.Settings.{name}", "", raising=False)


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


class TestResolveProfile:
    def test_auto_resolves_to_local_without_keys(self, monkeypatch):
        _no_remote_keys(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "auto")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "pearl")
        assert resolve_profile() == "local"

    def test_auto_resolves_to_hybrid_with_a_key(self, monkeypatch):
        _no_remote_keys(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "auto")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "pearl")
        monkeypatch.setattr("src.llm.router.Settings.ANTHROPIC_API_KEY", "sk-test")
        assert resolve_profile() == "hybrid"

    def test_explicit_global_provider_beats_auto_local(self, monkeypatch):
        """
        Regression: an explicitly chosen provider must never be silently
        replaced by the local model. This previously broke every test
        that set PEARL_LLM_PROVIDER=scripted — the router ignored it and
        tried to load a 1.1 GB GGUF instead.
        """
        _no_remote_keys(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "auto")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "scripted")
        assert resolve_profile() == "cloud"

    def test_explicit_profile_is_honoured(self, monkeypatch):
        for profile in ("local", "hybrid", "cloud"):
            monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", profile)
            assert resolve_profile() == profile

    def test_unknown_profile_degrades_to_local(self, monkeypatch):
        """Unknown values must fail toward the option needing nothing external."""
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "nonsense")
        assert resolve_profile() == "local"


# ---------------------------------------------------------------------------
# Role routing under each profile
# ---------------------------------------------------------------------------


class TestProfileRouting:
    def test_local_profile_keeps_every_role_local(self, monkeypatch):
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "local")
        for role in ("planning", "chat", "edit", "condenser", "reflection"):
            assert _effective_provider(role) == "pearl", role

    def test_hybrid_sends_reasoning_remote_and_keeps_chat_local(self, monkeypatch):
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "hybrid")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "anthropic")

        assert _effective_provider("planning") == "anthropic"
        assert _effective_provider("reflection") == "anthropic"
        assert _effective_provider("edit") == "anthropic"
        # Cheap/frequent roles stay local so the hybrid profile is not
        # just "cloud with extra steps".
        assert _effective_provider("chat") == "pearl"
        assert _effective_provider("condenser") == "pearl"

    def test_cloud_profile_sends_everything_to_global_provider(self, monkeypatch):
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "cloud")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        for role in ("planning", "chat", "edit", "condenser", "reflection"):
            assert _effective_provider(role) == "openai", role

    def test_explicit_role_setting_beats_profile(self, monkeypatch):
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "local")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "gemini")
        assert _effective_provider("planning") == "gemini"
        assert _effective_provider("chat") == "pearl"


# ---------------------------------------------------------------------------
# Autocomplete — the latency-critical role
# ---------------------------------------------------------------------------


class TestAutocompleteRouting:
    def test_autocomplete_stays_local_under_cloud_profile(self, monkeypatch):
        """
        A remote round-trip cannot meet the inline-completion latency
        budget, so this role ignores the profile.
        """
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "cloud")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "pearl")
        assert _effective_provider("autocomplete") == "pearl"

    def test_autocomplete_respects_explicit_override(self, monkeypatch):
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.AUTOCOMPLETE_PROVIDER", "openai")
        assert _effective_provider("autocomplete") == "openai"

    def test_autocomplete_uses_the_small_local_model(self, monkeypatch):
        monkeypatch.setattr("src.llm.router.Settings.PEARL_INFERENCE_API_KEY", "")
        monkeypatch.setattr("src.llm.router.Settings.LOCAL_MODEL_FILE", "big-1.5b.gguf")
        monkeypatch.setattr(
            "src.llm.router.Settings.AUTOCOMPLETE_LOCAL_MODEL_FILE", "small-0.5b.gguf"
        )
        assert _local_model_file("pearl", "autocomplete") == "small-0.5b.gguf"

    def test_other_roles_use_the_default_local_model(self, monkeypatch):
        monkeypatch.setattr("src.llm.router.Settings.PEARL_INFERENCE_API_KEY", "")
        assert _local_model_file("pearl", "planning") is None
        assert _local_model_file("pearl", "chat") is None

    def test_no_local_file_when_pearl_is_remote(self, monkeypatch):
        """With a gateway key, `pearl` is remote — no GGUF is involved."""
        monkeypatch.setattr("src.llm.router.Settings.PEARL_INFERENCE_API_KEY", "prl-key")
        assert _local_model_file("pearl", "autocomplete") is None
        assert _is_local_pearl("pearl") is False


# ---------------------------------------------------------------------------
# Vision — must refuse rather than degrade
# ---------------------------------------------------------------------------


class TestVisionRouting:
    def test_vision_client_is_none_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr("src.llm.router.Settings.VISION_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.VISION_MODEL", "")
        router = ModelRouter()
        assert router.vision_client() is None
        assert router.has_vision() is False

    def test_vision_never_falls_back_to_a_text_model(self, monkeypatch):
        """
        No bundled GGUF is multimodal. Falling back to one would produce
        confident descriptions of an image it never saw, so an
        unavailable vision provider must yield None, not a text client.
        """
        monkeypatch.setattr("src.llm.router.Settings.VISION_PROVIDER", "openai")
        monkeypatch.setattr("src.llm.router.Settings.VISION_MODEL", "gpt-4o")
        router = ModelRouter()
        with patch(
            "src.llm.router.create_provider", side_effect=RuntimeError("no key")
        ), patch(
            "src.llm.router.LLMClient", side_effect=RuntimeError("no key")
        ):
            assert router.vision_client() is None


# ---------------------------------------------------------------------------
# Local fallback — a cloud outage must not take Pearl down
# ---------------------------------------------------------------------------


class TestLocalFallback:
    def test_falls_back_to_local_when_remote_unavailable(self, monkeypatch):
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "cloud")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        monkeypatch.setattr("src.llm.router.Settings.MODEL_FALLBACK_TO_LOCAL", True)

        router = ModelRouter()
        built: list[str] = []
        sentinel = MagicMock()

        def _fake_build(task, provider_name):
            built.append(provider_name)
            if provider_name == "openai":
                raise RuntimeError("network down")
            return sentinel

        monkeypatch.setattr(router, "_build", _fake_build)

        assert router.client_for("planning") is sentinel
        assert built == ["openai", "pearl"], (
            "must attempt the configured provider, then fall back to local"
        )

    def test_fallback_can_be_disabled(self, monkeypatch):
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "cloud")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "openai")
        monkeypatch.setattr("src.llm.router.Settings.MODEL_FALLBACK_TO_LOCAL", False)

        router = ModelRouter()
        monkeypatch.setattr(
            router, "_build",
            MagicMock(side_effect=RuntimeError("network down")),
        )
        with pytest.raises(RuntimeError, match="network down"):
            router.client_for("planning")

    def test_local_failure_is_not_masked(self, monkeypatch):
        """A broken local model must surface, not loop into itself."""
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "local")
        monkeypatch.setattr("src.llm.router.Settings.MODEL_FALLBACK_TO_LOCAL", True)

        router = ModelRouter()
        monkeypatch.setattr(
            router, "_build",
            MagicMock(side_effect=RuntimeError("gguf corrupt")),
        )
        with pytest.raises(RuntimeError, match="gguf corrupt"):
            router.client_for("chat")


# ---------------------------------------------------------------------------
# describe() must not misreport what is running
# ---------------------------------------------------------------------------


class TestDescribe:
    def test_reports_the_actual_gguf_not_the_gateway_name(self, monkeypatch):
        """
        With no gateway key, `pearl` runs a local GGUF — but the
        PEARL_INFERENCE_*_MODEL settings still hold remote model names.
        Reporting those would tell the user a cloud model is running
        while nothing leaves the machine.
        """
        _clear_role_overrides(monkeypatch)
        monkeypatch.setattr("src.llm.router.Settings.MODEL_PROFILE", "local")
        monkeypatch.setattr("src.llm.router.Settings.PEARL_INFERENCE_API_KEY", "")
        monkeypatch.setattr("src.llm.router.Settings.LOCAL_MODEL_FILE", "qwen-1.5b.gguf")

        described = ModelRouter().describe()

        assert described["chat"] == "local/qwen-1.5b.gguf"
        assert "anthropic/" not in described["chat"]
        assert "claude" not in described["chat"].lower()

    def test_includes_profile_and_every_role(self, monkeypatch):
        described = ModelRouter().describe()
        assert "profile" in described
        for role in (
            "planning", "chat", "edit", "condenser",
            "autocomplete", "reflection", "vision",
        ):
            assert role in described, f"describe() omitted {role}"

    def test_unconfigured_vision_is_labelled(self, monkeypatch):
        monkeypatch.setattr("src.llm.router.Settings.VISION_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.VISION_MODEL", "")
        assert ModelRouter().describe()["vision"] == "unconfigured"

    def test_never_leaks_credentials(self, monkeypatch):
        """describe() feeds the settings UI — it must expose names only."""
        monkeypatch.setattr(
            "src.llm.router.Settings.ANTHROPIC_API_KEY", "sk-ant-SECRET123"
        )
        blob = " ".join(ModelRouter().describe().values())
        assert "SECRET123" not in blob
        assert "sk-ant" not in blob


# ---------------------------------------------------------------------------
# Multi-model local cache
# ---------------------------------------------------------------------------


class TestLocalModelCache:
    def test_same_model_is_loaded_once(self, monkeypatch):
        """Identical (path, n_ctx) must share one instance — the original
        RAM-saving guarantee, which keying must not regress."""
        import src.llm.providers.local_inference as mod

        loaded: list[str] = []

        class _FakeLlama:
            def __init__(self, model_path, n_ctx, n_threads, verbose):
                loaded.append(model_path)
            def n_ctx(self):
                return 512

        with patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=_FakeLlama)}):
            mod.reset_model_cache()
            try:
                a = mod._get_shared_llm("m.gguf", 512, 1)
                b = mod._get_shared_llm("m.gguf", 512, 1)
                assert a is b
                assert len(loaded) == 1, "the same model must not load twice"
            finally:
                mod.reset_model_cache()

    def test_distinct_models_load_separately(self, monkeypatch):
        """
        The blocker this fixed: a single global singleton returned the
        first-loaded model for every request, so autocomplete would have
        silently run the large planning model.
        """
        import src.llm.providers.local_inference as mod

        class _FakeLlama:
            def __init__(self, model_path, n_ctx, n_threads, verbose):
                self.path = model_path
            def n_ctx(self):
                return 512

        with patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=_FakeLlama)}):
            mod.reset_model_cache()
            try:
                small = mod._get_shared_llm("small.gguf", 512, 1)
                large = mod._get_shared_llm("large.gguf", 512, 1)
                assert small is not large
                assert small.path.endswith("small.gguf")
                assert large.path.endswith("large.gguf")
            finally:
                mod.reset_model_cache()

    def test_cache_is_bounded(self, monkeypatch):
        """A bad config must not load models until memory runs out."""
        import src.llm.providers.local_inference as mod

        class _FakeLlama:
            def __init__(self, model_path, n_ctx, n_threads, verbose):
                pass
            def n_ctx(self):
                return 512

        monkeypatch.setattr(
            "src.config.settings.Settings.LOCAL_MAX_LOADED_MODELS", 2
        )
        with patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=_FakeLlama)}):
            mod.reset_model_cache()
            try:
                for name in ("a.gguf", "b.gguf", "c.gguf"):
                    mod._get_shared_llm(name, 512, 1)
                assert len(mod._llms) == 2, "cache must evict beyond the cap"
            finally:
                mod.reset_model_cache()
