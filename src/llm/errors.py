"""
Typed LLM error hierarchy for Pearl.

Separating these from ``client.py`` lets providers import and raise them
without creating a circular import through the full client stack.
"""

from __future__ import annotations


class ContextLengthError(RuntimeError):
    """
    Input prompt exceeds the model's context window.

    Raised by providers when the tokenised messages are longer than
    ``n_ctx`` (local inference) or the API's documented maximum
    (hosted providers).

    Catching this specifically — rather than bare ``Exception`` — lets
    the condenser intercept it, compress the conversation, and retry the
    same operation rather than surfacing a cryptic error to the user.
    """


class ProviderAuthError(RuntimeError):
    """
    The configured provider rejected Pearl's credentials, or has none.

    Providers declare their own SDK's authentication exceptions in
    ``LLMProvider.AUTH_ERRORS``; ``LLMClient`` catches those and re-raises
    this instead. That keeps vendor SDK types inside ``src/llm/providers/``
    — a caller in the protocol or agent layer that wants to show a setup
    message catches this one type rather than importing ``openai`` to get
    at ``OpenAIError`` (Rule LLM-1).

    Never retried. A rejected credential is not a transient condition;
    retrying it just fails again three times more slowly.
    """
