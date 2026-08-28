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

TaskKind = str  # "planning" | "chat"


def _effective_provider(task: TaskKind) -> str:
    """
    Return the provider name for `task`, falling back to the global
    setting when the task-specific one is absent.
    """

    if task == "planning" and Settings.PLANNING_PROVIDER:
        return Settings.PLANNING_PROVIDER

    if task == "chat" and Settings.CHAT_PROVIDER:
        return Settings.CHAT_PROVIDER

    return Settings.LLM_PROVIDER


def _override_model(provider_name: str, task: TaskKind) -> str | None:
    """
    Return the model override for `task`, or ``None`` when the global
    provider's own default should be used.
    """

    if task == "planning" and Settings.PLANNING_MODEL:
        return Settings.PLANNING_MODEL

    if task == "chat" and Settings.CHAT_MODEL:
        return Settings.CHAT_MODEL

    return None


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

    def client_for(self, task: TaskKind) -> LLMClient:
        """
        Return the ``LLMClient`` for `task`.

        Parameters
        ----------
        task:
            ``"planning"`` or ``"chat"``.  Unknown values fall back to
            the global provider (same as the ``"chat"`` path).
        """

        if task not in self._clients:
            provider_name = _effective_provider(task)
            model_override = _override_model(provider_name, task)

            if model_override:
                provider = create_provider(provider_name)
                provider.model = model_override  # type: ignore[attr-defined]
                llm = LLMClient(provider=provider)
                logger.info(
                    "ModelRouter: task=%r → provider=%r model=%r",
                    task,
                    provider_name,
                    model_override,
                )
            else:
                llm = LLMClient(provider_name=provider_name)
                logger.info(
                    "ModelRouter: task=%r → provider=%r (default model)",
                    task,
                    provider_name,
                )

            self._clients[task] = llm

        return self._clients[task]

    def planning_client(self) -> LLMClient:
        """Return the ``LLMClient`` configured for planning tasks."""
        return self.client_for("planning")

    def chat_client(self) -> LLMClient:
        """Return the ``LLMClient`` configured for chat tasks."""
        return self.client_for("chat")

    def all_same_provider(self) -> bool:
        """
        Return ``True`` when planning and chat are configured to use
        the same provider, so callers that already have one ``LLMClient``
        don't need to build a second one.
        """
        return _effective_provider("planning") == _effective_provider("chat") and not (
            Settings.PLANNING_MODEL or Settings.CHAT_MODEL
        )
