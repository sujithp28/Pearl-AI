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
