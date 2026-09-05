"""
Inline code completion for Pearl.

Turns a cursor position into a single suggested continuation, using the
autocomplete model tier (a small local model by default — see
`src/llm/router.py`).

Why this is separate from the chat/planning path
------------------------------------------------
Completion has the opposite requirements to everything else Pearl does:

* It must answer in well under a second, or the suggestion arrives after
  the developer has already typed past it.
* It must *continue* text rather than talk about it, so it uses
  `LLMClient.complete_raw()` (no chat template) rather than `generate()`.
  An instruct model handed a bare code fragment through a chat template
  answers about the code, often refusing it outright.
* It is fired on almost every keystroke, so identical requests must not
  re-run inference, and in-flight work must be cheap to abandon.

None of that is true of planning, so none of it belongs in the planner.

Not implemented: fill-in-the-middle
-----------------------------------
The bundled model (Qwen2.5-*-Instruct) is not FIM-tuned — it has no
`<|fim_prefix|>`/`<|fim_suffix|>` tokens to key on, so text after the
cursor cannot be injected as true FIM context. `suffix` is still used,
for overlap trimming and stop-sequence derivation, which are real wins;
a Coder-tuned model would additionally allow proper FIM prompting.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# How much text either side of the cursor is sent to the model. The
# autocomplete model runs with a deliberately small context (2048), and
# more prefix mostly buys latency rather than accuracy for a single-line
# suggestion, so this stays well under the window.
MAX_PREFIX_CHARS = 2000
MAX_SUFFIX_CHARS = 500

# Completions are requested on nearly every keystroke; identical requests
# must not re-run inference. Small on purpose — this is a keystroke-local
# cache, not a corpus.
_CACHE_SIZE = 128

# Stop as soon as the model leaves the construct it was completing. A
# blank line or a new top-level definition means the useful part of the
# suggestion has already been produced.
_DEFAULT_STOP = ["\n\n", "\ndef ", "\nclass ", "\n}", "\n\n\n"]

# Language-specific additions, keyed by the editor's language id.
_LANGUAGE_STOP: dict[str, list[str]] = {
    "python": ["\ndef ", "\nclass ", "\n@"],
    "javascript": ["\nfunction ", "\nexport ", "\nconst "],
    "typescript": ["\nfunction ", "\nexport ", "\nconst ", "\ninterface "],
    "typescriptreact": ["\nfunction ", "\nexport ", "\nconst "],
    "javascriptreact": ["\nfunction ", "\nexport ", "\nconst "],
    "go": ["\nfunc ", "\ntype "],
    "rust": ["\nfn ", "\nimpl ", "\nstruct "],
    "java": ["\npublic ", "\nprivate ", "\nprotected "],
}


@dataclass(frozen=True)
class CompletionResult:
    """One completion suggestion, plus why it looks the way it does."""

    text: str
    cached: bool = False
    #: Set when no suggestion was produced, so callers (and tests) can
    #: distinguish "the model had nothing useful" from "something broke".
    declined_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class CompletionService:
    """
    Produces inline completions from a cursor position.

    Thread-safe: the cache is guarded, and the underlying client
    serialises inference per model.
    """

    def __init__(self, client: object | None = None) -> None:
        # Resolved lazily so constructing the service never loads a model —
        # the MCP/HTTP servers build one at startup, long before any
        # completion is requested, and may never receive one at all.
        self._client = client
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────────────

    def complete(
        self,
        prefix: str,
        suffix: str = "",
        language: str = "",
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """
        Return a single completion for the text at the cursor.

        `prefix` is the text before the cursor, `suffix` the text after.
        Never raises for model or configuration failures — a completion
        that cannot be produced returns an empty result with a reason,
        because a failed suggestion must never interrupt typing.
        """
        if not self._should_complete(prefix):
            return CompletionResult("", declined_reason="trivial-context")

        prefix = prefix[-MAX_PREFIX_CHARS:]
        suffix = suffix[:MAX_SUFFIX_CHARS]

        key = (prefix, suffix)
        cached = self._cache_get(key)
        if cached is not None:
            return CompletionResult(cached, cached=True)

        try:
            raw = self._infer(prefix, language, max_tokens)
        except Exception as exc:
            # Deliberately broad: this runs on the typing path, and no
            # model/provider failure is worth surfacing as an editor error.
            logger.debug("Completion failed: %s", exc, exc_info=True)
            return CompletionResult("", declined_reason=f"error: {exc}")

        text = self._postprocess(raw, prefix, suffix)

        if text:
            self._cache_put(key, text)

        return CompletionResult(
            text,
            declined_reason=None if text else "empty-after-postprocessing",
        )

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # ── Internals ────────────────────────────────────────────────────────────

    def _resolve_client(self) -> object:
        if self._client is None:
            from src.llm.router import ModelRouter

            self._client = ModelRouter().autocomplete_client()
        return self._client

    def _infer(self, prefix: str, language: str, max_tokens: int | None) -> str:
        client = self._resolve_client()
        stop = _DEFAULT_STOP + _LANGUAGE_STOP.get(language.lower(), [])

        return client.complete_raw(  # type: ignore[attr-defined]
            prefix,
            temperature=0.0,  # deterministic: the same context should suggest the same thing
            max_new_tokens=max_tokens or Settings.AUTOCOMPLETE_MAX_TOKENS,
            stop=stop,
        )

    @staticmethod
    def _should_complete(prefix: str) -> bool:
        """
        Cheap gates applied before any inference.

        Suggesting into an empty buffer, or immediately after the user
        typed a blank line, produces noise rather than help — and every
        avoided call is latency the developer does not wait for.
        """
        if not prefix.strip():
            return False
        # Just opened a fresh blank line after a blank line: nothing to continue.
        if prefix.endswith("\n\n"):
            return False
        return True

    @staticmethod
    def _postprocess(raw: str, prefix: str, suffix: str) -> str:
        """
        Clean a raw model continuation into something safe to insert.

        Handles the three ways a small model's output is unusable even
        when the prediction itself was reasonable: markdown fences it was
        never asked for, repeating text the user already typed, and
        re-emitting the text that already follows the cursor.
        """
        text = raw

        # Small instruct-tuned models sometimes still emit fences even on
        # the raw path, having seen them constantly during training.
        text = re.sub(r"^\s*```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

        if not text.strip():
            return ""

        # Never re-suggest what the user already typed. Models frequently
        # restate the tail of the prompt before continuing it.
        text = CompletionService._strip_prefix_overlap(text, prefix)

        # Without FIM the model cannot see the suffix, so it will happily
        # regenerate the line that already follows the cursor.
        text = CompletionService._strip_suffix_overlap(text, suffix)

        # Trailing whitespace is never useful in a suggestion and shows up
        # as a stray indent in the editor's ghost text.
        return text.rstrip()

    @staticmethod
    def _strip_prefix_overlap(text: str, prefix: str) -> str:
        """Drop a leading repeat of the end of `prefix`."""
        tail = prefix[-120:]
        # Longest first: a longer overlap is the more specific match.
        for size in range(min(len(tail), len(text)), 4, -1):
            if text.startswith(tail[-size:]):
                return text[size:]
        return text

    @staticmethod
    def _strip_suffix_overlap(text: str, suffix: str) -> str:
        """Drop a trailing repeat of the start of `suffix`."""
        if not suffix.strip():
            return text
        head = suffix[:120]
        for size in range(min(len(head), len(text)), 4, -1):
            if text.endswith(head[:size]):
                return text[:-size]
        return text

    def _cache_get(self, key: tuple[str, str]) -> str | None:
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def _cache_put(self, key: tuple[str, str], value: str) -> None:
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
