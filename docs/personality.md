# Pearl's Personality Engine

Pearl's own status/progress messages ("Planning...", "Tests passed.",
"Patch ready.") can be given a configurable voice and emoji density.
This is a **presentation-only** layer: it never touches planning,
tool selection, tool execution, code generation, or anything that
goes into or comes out of an LLM call. See [Hard boundary](#hard-boundary)
below for exactly what that guarantee means and how it's enforced.

## Configuration

Two environment variables, both optional:

```bash
PERSONALITY=cheeky      # professional | friendly | cheeky | savage (default: cheeky)
EMOJI_MODE=minimal      # none | minimal | normal | fun            (default: minimal)
```

An unrecognized value for either falls back to a safe default
(`professional` for personality, `minimal` for emoji mode) rather than
raising — a typo in `.env` should never crash Pearl, it should just
quietly use the calmest option.

Changing personality doesn't require a restart: `PersonalityManager`
reads `Settings` once at construction, but also exposes
`set_personality()`/`set_emoji_mode()` for runtime changes (e.g. a
future in-app settings toggle).

## The four personalities

| Personality | Tone | Example (`completed`) |
|---|---|---|
| `professional` | Calm, minimal emotion, enterprise-appropriate | "Task completed successfully." |
| `friendly` | Warm, encouraging, still concise | "Nice! Everything finished successfully." |
| `cheeky` (default) | Confident, playful, jokes about *code*, never about the user | "Mission accomplished. Coffee for you. Electrons for me." |
| `savage` | Maximum developer humor, still respectful — roasts code, never people | "The code has finally agreed to behave." |

`savage` and `cheeky` never insult the user, shame beginners, use
profanity, or attack anyone — only the code itself is fair game. See
`src/personality/personalities.py` for the full set of messages.

## Emoji modes

| Mode | Behavior |
|---|---|
| `none` | No emoji at all. |
| `minimal` (default) | One leading category emoji per message (e.g. "🧪 Running tests."). |
| `normal` / `fun` | Same today; reserved for future messages that embed a second, situational emoji. |

Regardless of mode, every message is capped at **two emoji maximum**,
never repeats the same emoji, and never decorates inside a fenced code
block, inline code span, or JSON/YAML-shaped text — enforced by
`enforce_emoji_limit()` and `apply_emoji_safely()`
(`src/personality/formatter.py`), not just by careful authoring.

## Automatic serious mode

Humor is force-disabled — the message renders in `professional` tone
regardless of the configured personality — in two cases:

1. **`EventKind.SECURITY`** — any security-related status message is
   always serious, unconditionally (`serious_mode.ALWAYS_SERIOUS_EVENT_KINDS`).
2. **An explicit `serious=True`** passed to `PersonalityManager.format()`
   — for situations serious_mode can't infer from the event kind alone
   (data loss, destructive operations, authentication failures, ...).
   A caller that knows it's in one of these situations says so
   explicitly; this is a closed, explicit set on purpose, not a
   heuristic scan of message text (unreliable, and exactly the kind of
   expensive, unpredictable processing the formatter is required to
   avoid).

Serious mode only affects that one `format()` call — it never changes
the manager's configured personality for anything after it.

## Architecture

```
src/personality/
    __init__.py       Public exports; the "hard boundary" contract (see below)
    manager.py         PersonalityManager — the one object callers use
    formatter.py        format_event() / enforce_emoji_limit() / apply_emoji_safely()
    personalities.py    Personality enum + the full message table
    emoji.py             EventKind enum + emoji mapping + EmojiMode
    templates.py         Safe {placeholder} substitution (no KeyError on typos)
    serious_mode.py       is_serious() + the always-serious event set
```

Everything except `manager.py` is a pure data table or a pure
function — no I/O, no shared mutable state, nothing that could be slow
or non-deterministic. `PersonalityManager` itself only stores which
personality/emoji-mode is currently active; `format()` is a handful of
dict lookups and short regex passes, safe to call from multiple
threads concurrently without a lock (a lock only guards the rare
`set_personality`/`set_emoji_mode` writes).

### Formatter pipeline

```
EventKind + Personality  →  PERSONALITY_TEMPLATES lookup  →  render() (placeholder substitution)
                                                                    │
                                            EmojiMode == NONE?  ────┤
                                              │no            │yes
                                              ▼               ▼
                                    prefix with EMOJI_MAP[event_kind]   (nothing)
                                              │
                                              ▼
                                    enforce_emoji_limit()  (cap 2, dedupe)
                                              │
                                              ▼
                                         final string
```

### Hard boundary

`src/personality/` never imports `src.llm` or `src.agent` — this is
enforced by a static test
(`tests/test_personality_isolation.py::test_personality_package_never_imports_llm_or_agent_modules`),
not just documented as a convention. Structurally, this package cannot
construct or inspect a prompt, and cannot see a model's output, even
if a future change tried to make it.

The one place personality is wired in today is
`AutonomousExecutor`'s `ProgressEvent.current_action` text
(`src/agent/executor.py`) — the executor calls
`self._personality.format(EventKind.X)` to build the *wording* of a
progress notification; the event's `status`, `current_step`,
`total_steps`, and everything about what actually ran (which tool,
its arguments, its result) are computed identically regardless of
personality. `tests/test_personality_isolation.py` asserts this
directly: the same plan run under `professional` and under `savage`
produces identical steps, results, and stop reasons — only the
progress text differs.

## Adding a new personality

1. Add a member to `Personality` (`personalities.py`).
2. Add one entry to `PERSONALITY_TEMPLATES` covering every `EventKind`
   — a test (`test_every_personality_defines_every_event_kind`) fails
   if any are missing.
3. Nothing else changes. `formatter.py`/`manager.py` do a plain dict
   lookup; there's no personality-specific branching anywhere to update.

## Adding a new emoji pack / event kind

1. Add a member to `EventKind` (`emoji.py`).
2. Add its emoji to `EMOJI_MAP`.
3. Add one message per personality to `PERSONALITY_TEMPLATES`.

## Known limitations

- Only wired into `AutonomousExecutor`'s progress narration today —
  not into `pearl/chat`'s free-form LLM replies, or into the VS Code
  extension's own canned strings. `apply_emoji_safely()`/
  `contains_code()` exist as safe primitives for a future integration
  into free-form text, but applying personality to arbitrary
  (potentially code-containing) LLM output safely is a materially
  harder problem than templating a fixed set of ~24 known-safe
  strings, and is out of scope for this pass.
- Serious-mode detection is keyed by explicit `EventKind`/`serious`
  flag, not automatic content inspection of arbitrary tool calls —
  e.g. nothing currently marks "this is a `git_restore` call" as
  inherently serious. Callers that need that must pass `serious=True`
  themselves.
