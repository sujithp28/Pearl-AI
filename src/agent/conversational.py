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
#
# Non-English greetings are included because the alternative is worse
# than it sounds: an unmatched greeting goes to the planner, and a small
# model asked to plan "namaste" invents a file to read and the run ends
# in an error. Someone greeting Pearl in their own language should not
# get a crash for it.
_PURE_CONVERSATIONAL = re.compile(
    r"""^\s*(?:
      # ── English ──────────────────────────────────────────────────
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

      # ── Romanised South Asian ────────────────────────────────────
      | (?:namaste|namaskar|namaskaram|vanakkam|sat\s?sri\s?akal|adaab)
      | (?:dhanyavaad|dhanyawad|shukriya|nandri|dhanyavadalu)
      | (?:kaise\s+ho|kya\s+haal|theek\s+hai|accha|thik\s+hai|haan|nahi)
      | (?:alvida|phir\s+milenge)

      # ── Other widely used greetings ──────────────────────────────
      | (?:hola|buenos\s+d[ií]as|buenas\s+(?:tardes|noches)|gracias|adi[oó]s)
      | (?:bonjour|bonsoir|salut|merci|au\s+revoir)
      | (?:hallo|guten\s+(?:tag|morgen|abend)|danke|tsch[üu]ss)
      | (?:ciao|buongiorno|grazie|arrivederci)
      | (?:ol[áa]|bom\s+dia|boa\s+(?:tarde|noite)|obrigad[oa])
      | (?:privet|zdravstvuyte|spasibo)
      | (?:salam|salaam|shukran|marhaba|assalamu?\s*alaikum)
      | (?:konnichiwa|ohayou?|arigatou?|sayonara)
      | (?:annyeong(?:haseyo)?|kamsahamnida)
      | (?:ni\s?hao|xiexie|zaijian)
      | (?:merhaba|te[sş]ekk[üu]rler)
      | (?:shalom|toda)

      # ── Non-Latin scripts ────────────────────────────────────────
      # Matched as whole words so a longer sentence in the same script
      # still reaches the planner rather than being treated as a greeting.
      | (?:नमस्ते|नमस्कार|धन्यवाद|शुक्रिया|अलविदा)
      | (?:வணக்கம்|நன்றி)
      | (?:నమస్కారం|ధన్యవాదాలు)
      | (?:নমস্কার|ধন্যবাদ)
      | (?:こんにちは|おはよう|ありがとう|さようなら)
      | (?:안녕하세요|안녕|감사합니다)
      | (?:你好|您好|谢谢|再见)
      | (?:مرحبا|السلام\s*عليكم|شكرا)
      | (?:привет|здравствуйте|спасибо|пока)
      | (?:γεια|ευχαριστώ)
    )
    [\s!?.,~]*$""",
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
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
