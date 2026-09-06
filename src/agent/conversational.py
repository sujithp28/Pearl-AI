"""
Detects input that needs no tools, so it never enters the planning loop.

A greeting is not an engineering task, but a small model asked to plan
one will invent work rather than decline: "hello" reliably produced
`read_file("hello.py")`, which failed, replanned, and surfaced as
`fatal_error`. The user saw a crash in response to saying hi.

Pearl already has a first-class answer for "no tool is needed" — the
``none`` sentinel step, supported by the validator, the confidence
scorer, the executor, and the synthesizer ("No tools were used —
respond conversationally"). Nothing new is required; the model simply
has to choose it, and a 1.5B often does not.

So this is a deterministic short-circuit rather than a classifier: no
model call, no added latency, and no chance of an LLM misjudging it
differently on two identical inputs. That also makes a greeting return
instantly instead of after a multi-second planning call.

The bar for matching is deliberately high. A false positive silently
refuses to do real work, which is far worse than a false negative —
those merely fall through to normal planning, exactly as before.
"""
from __future__ import annotations

import re

# Whole-input courtesy phrases. Anchored: these must be the *entire*
# message, so "hi, delete the build directory" is never caught by "hi".
_PURE_CONVERSATIONAL = re.compile(
    r"""^\s*(?:
        (?:hi|hii+|hey+|hello+|helo|yo|sup|hiya|howdy)
      | (?:good\s+(?:morning|afternoon|evening|day))
      | (?:how\s+(?:are\s+you|is\s+it\s+going|are\s+things)|how'?s\s+it\s+going)
      | (?:thanks?|thank\s+you|thx|ty|cheers|nice|cool|great|awesome|perfect)
      | (?:ok|okay|k|kk|sure|yep|yes|yeah|no|nope|got\s+it|sounds\s+good)
      | (?:bye+|goodbye|see\s+you|cya|later|good\s?night)
      | (?:ping)
      # Deliberately NOT "test"/"testing": those plausibly mean "run the
      # tests", and silently refusing real work is the costly direction.
      # _TASK_SIGNAL catches them anyway; listing them here would only be
      # misleading.
      | (?:who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do)
    )
    [\s!?.,~]*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Anything suggesting real work overrides the match above. Cheap
# insurance against a courtesy word carrying a request behind it.
_TASK_SIGNAL = re.compile(
    r"""(?:
        [/\\]                      # a path
      | \.\w{1,5}\b                # a file extension
      | \b(?:def|class|function|import|const|fn|func)\b
      | [(){}\[\]=<>]              # code punctuation
      | \b(?:fix|add|create|write|delete|remove|update|change|refactor
            |run|test|build|install|show|list|find|search|read|open
            |explain|implement|make|check|review|debug|why|how\s+do
            |what\s+does|where\s+is)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Beyond this, treat it as a real request even if it looks chatty —
# length itself is evidence of substance.
_MAX_WORDS = 6


def needs_no_tools(prompt: str) -> bool:
    """
    Return True when `prompt` is plainly conversational.

    Callers should answer it directly (or plan a single ``none`` step)
    rather than running the autonomous loop. Returns False whenever
    there is any doubt: falling through to normal planning is the safe
    direction, since it is what happened before this check existed.
    """
    if not prompt or not prompt.strip():
        return False

    text = prompt.strip()

    # Length gate first — it is the cheapest and rules out most real work.
    if len(text.split()) > _MAX_WORDS:
        return False

    if _TASK_SIGNAL.search(text):
        return False

    return bool(_PURE_CONVERSATIONAL.match(text))
