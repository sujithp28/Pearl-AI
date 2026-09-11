"""
Model router for Pearl (M6).

``ModelRouter`` produces the right ``LLMClient`` for each task type —
``planning`` (JSON-structured tool-call plans that demand accuracy) and
``chat`` (conversational responses that benefit from speed).

When task-specific provider / model settings are absent, both task
types fall back to the global ``PEARL_LLM_PROVIDER`` — so the router
is a transparent no-op for users who have not configured routing.

Why a router instead of two separate client fields in every caller?

*  Callers (``MCPServer``, ``PearlAgent``) deal with one ``ModelRouter``
   instead of two ``LLMClient`` objects, keeping the constructor
   signatures short.
*  The routing logic and its fallbacks live in exactly one place.
*  Adding a third task type (e.g. ``embedding`` or ``reranking``) later
   is a one-file change.

Usage::

    from src.llm.router import ModelRouter

    router = ModelRouter()
    planning_llm = router.planning_client()
    chat_llm      = router.chat_client()
"""

from __future__ import annotations

import logging

from src.config.settings import Settings
from src.llm.client import LLMClient
from src.llm.providers.factory import create_provider

logger = logging.getLogger(__name__)

TaskKind = str  # "planning" | "chat" | "edit" | "condenser" | "autocomplete" | "vision" | "reflection"

# Roles whose latency budget rules out a network round-trip.  These stay
# local regardless of profile — a cloud autocomplete is not a slower
# autocomplete, it is an unusable one.
_LATENCY_CRITICAL: frozenset[str] = frozenset({"autocomplete"})

# Roles that benefit from reasoning strength over speed.  Under the
# "hybrid" profile these are the ones that go remote.
_REASONING_ROLES: frozenset[str] = frozenset({"planning", "reflection", "edit"})

# The local, zero-config provider name.
_LOCAL_PROVIDER = "pearl"


def _explicit_provider(task: TaskKind) -> str:
    """Return the user's explicit per-role provider, or "" when unset."""
    return {
        "planning": Settings.PLANNING_PROVIDER,
        "chat": Settings.CHAT_PROVIDER,
        "edit": Settings.EDIT_PROVIDER,
        "condenser": Settings.CONDENSER_PROVIDER,
        "autocomplete": Settings.AUTOCOMPLETE_PROVIDER,
        "vision": Settings.VISION_PROVIDER,
        "reflection": Settings.REFLECTION_PROVIDER,
    }.get(task, "")


def _remote_key_configured() -> bool:
    """
    Return whether any BYOK remote provider has credentials.

    Used only to resolve the "auto" profile; never exposes or logs the
    key itself — just whether one is present.
    """
    return any(
        bool(getattr(Settings, name, ""))
        for name in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "GEMINI_API_KEY",
            "PEARL_INFERENCE_API_KEY",
        )
    )


def resolve_profile() -> str:
    """
    Resolve ``Settings.MODEL_PROFILE`` to a concrete profile name.

    ``"auto"`` becomes ``"local"`` with no remote credentials and
    ``"hybrid"`` with them, so a fresh install works offline and a
    configured one gets better reasoning without extra setup.
    Unrecognised values degrade to ``"local"`` — the safe direction,
    since it needs nothing external.
    """
    profile = Settings.MODEL_PROFILE

    if profile == "auto":
        # An explicitly chosen global provider is a decision, not a
        # default — honour it for every role instead of forcing local.
        # Without this, setting PEARL_LLM_PROVIDER=openai (or scripted,
        # in tests) would be silently ignored.
        if Settings.LLM_PROVIDER != _LOCAL_PROVIDER:
            return "cloud"
        return "hybrid" if _remote_key_configured() else "local"

    if profile in ("local", "hybrid", "cloud"):
        return profile

    logger.warning("Unknown PEARL_MODEL_PROFILE=%r; falling back to 'local'.", profile)
    return "local"


def _profile_provider(task: TaskKind) -> str:
    """
    Return the provider `task` should use under the active profile.

    Explicit per-role settings are handled by the caller and take
    precedence; this is only consulted when the user has not chosen.
    """
    # Latency-critical roles prefer local, since a network round-trip
    # cannot meet their budget — but an explicitly chosen global
    # provider still wins, so tests and deliberate configuration are
    # never silently overridden.
    if task in _LATENCY_CRITICAL:
        if Settings.LLM_PROVIDER != _LOCAL_PROVIDER:
            return Settings.LLM_PROVIDER
        return _LOCAL_PROVIDER

    profile = resolve_profile()

    if profile == "local":
        return _LOCAL_PROVIDER

    if profile == "cloud":
        return Settings.LLM_PROVIDER

    # hybrid: reasoning goes remote, everything else stays local/cheap.
    if task in _REASONING_ROLES:
        return Settings.LLM_PROVIDER

    return _LOCAL_PROVIDER


def _effective_provider(task: TaskKind) -> str:
    """
    Return the provider name for `task`.

    Precedence: explicit per-role setting → role's own fallback chain →
    active profile → global provider.
    """

    explicit = _explicit_provider(task)
    if explicit:
        return explicit

    # Derived roles inherit from the role they specialise, so that
    # configuring "chat" alone still moves them together.
    if task in ("edit", "condenser") and Settings.CHAT_PROVIDER:
        return Settings.CHAT_PROVIDER

    if task == "reflection" and Settings.CHAT_PROVIDER:
        return Settings.CHAT_PROVIDER

    return _profile_provider(task)


def _override_model(provider_name: str, task: TaskKind) -> str | None:
    """
    Return the model override for `task`, or ``None`` when the global
    provider's own default should be used.
    """

    explicit = {
        "planning": Settings.PLANNING_MODEL,
        "chat": Settings.CHAT_MODEL,
        "edit": Settings.EDIT_MODEL,
        "condenser": Settings.CONDENSER_MODEL_NAME,
        "autocomplete": Settings.AUTOCOMPLETE_MODEL,
        "vision": Settings.VISION_MODEL,
        "reflection": Settings.REFLECTION_MODEL,
    }.get(task, "")
    if explicit:
        return explicit

    # Pearl provider routes each task to a dedicated model tier so that
    # planning uses a more capable model than chat without any per-user config.
    if provider_name == "pearl":
        if task == "planning":
            return Settings.PEARL_INFERENCE_PLAN_MODEL
        # Autocomplete uses the smallest local model: at 64 tokens of
        # output, a 0.5B answers in a fraction of the 1.5B's time and
        # the quality difference barely shows on a single line.
        if task == "autocomplete":
            return Settings.AUTOCOMPLETE_LOCAL_MODEL_FILE
        return Settings.PEARL_INFERENCE_CHAT_MODEL

    return None


def _is_local_pearl(provider_name: str) -> bool:
    """
    Return whether `provider_name` resolves to on-device inference.

    The ``pearl`` provider is remote when a gateway key is present and
    local otherwise — the same name means different things depending on
    configuration, so this must be asked rather than assumed.
    """
    return provider_name == _LOCAL_PROVIDER and not Settings.PEARL_INFERENCE_API_KEY


def _local_model_file(provider_name: str, task: TaskKind) -> str | None:
    """
    Return the GGUF filename `task` should load, or ``None`` when this
    task is not served by local inference.

    Only returns a value when the file differs from the default, so the
    common path keeps using ``create_provider`` unchanged.
    """
    if not _is_local_pearl(provider_name):
        return None

    if task == "autocomplete":
        chosen = Settings.AUTOCOMPLETE_LOCAL_MODEL_FILE
        return chosen if chosen != Settings.LOCAL_MODEL_FILE else None

    return None


def _create_local_provider(filename: str, task: TaskKind):
    """
    Build a ``LocalInferenceProvider`` bound to a specific GGUF.

    Autocomplete gets a deliberately small context: it only ever sees a
    window around the cursor, and a smaller n_ctx allocates far less RAM
    and starts faster.
    """
    from pathlib import Path

    from src.llm.providers.local_inference import LocalInferenceProvider

    n_ctx = 2048 if task == "autocomplete" else Settings.LOCAL_MODEL_CTX

    return LocalInferenceProvider(
        model_path=str(Path(Settings.LOCAL_MODEL_DIR).expanduser() / filename),
        repo_id=Settings.LOCAL_MODEL_REPO,
        filename=filename,
        n_ctx=n_ctx,
    )


class ModelRouter:
    """
    Constructs and caches ``LLMClient`` instances per task type.

    The first call to :meth:`planning_client` (or :meth:`chat_client`)
    builds the client; subsequent calls return the same object so the
    underlying provider connection is established once, not on every
    request.

    Injecting a ``ModelRouter`` into ``MCPServer`` / ``PearlAgent``
    (rather than letting them each build their own ``LLMClient``)
    makes test overrides and provider swaps a single-call change.
    """

    def __init__(self) -> None:
        self._clients: dict[TaskKind, LLMClient] = {}
        # Roles that resolve to the same provider, model and local GGUF
        # are the same client. Keying only by role would build a second
        # one per role — and for local inference that means loading the
        # same multi-gigabyte model into memory twice.
        self._by_identity: dict[tuple[str, str | None, str | None], LLMClient] = {}

    @staticmethod
    def _identity(
        task: TaskKind, provider_name: str
    ) -> tuple[str, str | None, str | None]:
        """
        Return what actually distinguishes one client from another:
        the provider, the model override, and the local GGUF file.
        """

        return (
            provider_name,
            _override_model(provider_name, task),
            _local_model_file(provider_name, task),
        )

    def client_for(self, task: TaskKind) -> LLMClient:
        """
        Return the ``LLMClient`` for `task`.

        Roles resolving to an identical provider/model share one client.

        Parameters
        ----------
        task:
            ``"planning"`` or ``"chat"``.  Unknown values fall back to
            the global provider (same as the ``"chat"`` path).
        """

        if task not in self._clients:
            provider_name = _effective_provider(task)

            identity = self._identity(task, provider_name)
            shared = self._by_identity.get(identity)
            if shared is not None:
                logger.debug(
                    "ModelRouter: task=%r reuses the client for %r", task, identity
                )
                self._clients[task] = shared
                return shared

            try:
                client = self._build(task, provider_name)
                self._clients[task] = client
                self._by_identity[identity] = client
            except Exception as exc:
                # A misconfigured or unreachable remote provider must not
                # take Pearl down — zero-config local inference is the
                # floor the product guarantees.  Vision is excluded by
                # its own accessor, since no local model can stand in.
                if (
                    not Settings.MODEL_FALLBACK_TO_LOCAL
                    or provider_name == _LOCAL_PROVIDER
                    or task == "vision"
                ):
                    raise
                logger.warning(
                    "ModelRouter: task=%r provider=%r unavailable (%s); "
                    "falling back to local inference.",
                    task,
                    provider_name,
                    exc,
                )
                fallback_identity = self._identity(task, _LOCAL_PROVIDER)
                fallback = self._by_identity.get(fallback_identity)
                if fallback is None:
                    fallback = self._build(task, _LOCAL_PROVIDER)
                    self._by_identity[fallback_identity] = fallback
                self._clients[task] = fallback

        return self._clients[task]

    def _build(self, task: TaskKind, provider_name: str) -> LLMClient:
        """Construct the client for `task` on `provider_name`."""
        model_override = _override_model(provider_name, task)

        # Local inference selects its model by FILE PATH at construction —
        # assigning `.model` afterwards only changes a display label and
        # would silently run the wrong GGUF. Build it explicitly instead.
        local_file = _local_model_file(provider_name, task)
        if local_file is not None:
            provider = _create_local_provider(local_file, task)
            logger.info("ModelRouter: task=%r → local model %r", task, local_file)
            return LLMClient(provider=provider)

        if model_override:
            provider = create_provider(provider_name)
            provider.model = model_override  # type: ignore[attr-defined]
            logger.info(
                "ModelRouter: task=%r → provider=%r model=%r",
                task,
                provider_name,
                model_override,
            )
            return LLMClient(provider=provider)

        logger.info(
            "ModelRouter: task=%r → provider=%r (default model)",
            task,
            provider_name,
        )
        return LLMClient(provider_name=provider_name)

    def describe(self) -> dict[str, str]:
        """
        Return ``{role: "provider/model"}`` for every role.

        Used by ``/api/provider`` and the settings UI to show what is
        actually running.  Never includes credentials — only names.
        """
        roles = (
            "planning",
            "chat",
            "edit",
            "condenser",
            "autocomplete",
            "reflection",
            "vision",
        )
        out: dict[str, str] = {"profile": resolve_profile()}
        for role in roles:
            if role == "vision" and not (
                Settings.VISION_PROVIDER or Settings.VISION_MODEL
            ):
                out[role] = "unconfigured"
                continue
            provider = _effective_provider(role)
            if _is_local_pearl(provider):
                # Report the GGUF actually loaded. The gateway model
                # names in PEARL_INFERENCE_*_MODEL do not apply on-device,
                # and showing them would tell the user a remote model is
                # running when nothing leaves the machine.
                out[role] = (
                    f"local/{_local_model_file(provider, role) or Settings.LOCAL_MODEL_FILE}"
                )
                continue
            model = _override_model(provider, role) or "(default)"
            out[role] = f"{provider}/{model}"
        return out

    def planning_client(self) -> LLMClient:
        """Return the ``LLMClient`` configured for planning tasks."""
        return self.client_for("planning")

    def chat_client(self) -> LLMClient:
        """Return the ``LLMClient`` configured for chat tasks."""
        return self.client_for("chat")

    def edit_client(self) -> LLMClient:
        """
        Return the ``LLMClient`` for code editing (editor model).

        Falls back to chat_client() when no separate edit provider is configured,
        ensuring zero-config behavior is preserved.
        """
        # Only allocate a separate client when edit is distinctly configured.
        if Settings.EDIT_PROVIDER or Settings.EDIT_MODEL:
            return self.client_for("edit")
        return self.chat_client()

    def condenser_client(self) -> LLMClient:
        """
        Return the ``LLMClient`` for conversation condensation.

        Falls back to chat_client() when no separate condenser provider is
        configured.  The condenser should use a cheap/fast model.
        """
        if Settings.CONDENSER_PROVIDER or Settings.CONDENSER_MODEL_NAME:
            return self.client_for("condenser")
        return self.chat_client()

    def autocomplete_client(self) -> LLMClient:
        """
        Return the ``LLMClient`` for inline code completion.

        Always local unless explicitly overridden: a network round-trip
        cannot meet the latency budget that makes inline completion
        feel usable, so this role deliberately ignores the profile.
        """
        return self.client_for("autocomplete")

    def reflection_client(self) -> LLMClient:
        """
        Return the ``LLMClient`` for reflection.

        Reflection judges whether a task actually completed, which is a
        reasoning task — under the hybrid profile it goes remote even
        though it is bookkeeping rather than user-facing.
        """
        if Settings.REFLECTION_PROVIDER or Settings.REFLECTION_MODEL:
            return self.client_for("reflection")
        return self.chat_client()

    def vision_client(self) -> LLMClient | None:
        """
        Return the ``LLMClient`` for multimodal input, or ``None``.

        Returns ``None`` when no vision-capable provider is configured.
        There is no local fallback: no bundled GGUF is multimodal, so
        degrading to a text model would produce confident descriptions
        of an image it never saw.  Callers must handle ``None`` by
        refusing the image, not by pretending.
        """
        if not (Settings.VISION_PROVIDER or Settings.VISION_MODEL):
            return None
        try:
            return self.client_for("vision")
        except Exception as exc:
            logger.warning("Vision client unavailable: %s", exc)
            return None

    def has_vision(self) -> bool:
        """Return whether multimodal input is available in this config."""
        return self.vision_client() is not None

    def all_same_provider(self) -> bool:
        """
        Return ``True`` when planning and chat are configured to use
        the same provider, so callers that already have one ``LLMClient``
        don't need to build a second one.
        """
        if _effective_provider("planning") != _effective_provider("chat"):
            return False
        if Settings.PLANNING_MODEL or Settings.CHAT_MODEL:
            return False
        # Pearl uses different model tiers for planning vs chat — separate clients.
        if _effective_provider("planning") == "pearl":
            if (
                Settings.PEARL_INFERENCE_PLAN_MODEL
                != Settings.PEARL_INFERENCE_CHAT_MODEL
            ):
                return False
        return True
