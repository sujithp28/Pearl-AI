"""
Build a fine-tuning dataset for Pearl's planning model.

Why plan output and not something else
--------------------------------------
The bundled 1.5B does not fail at coding knowledge — it fails at
producing a valid plan. `src/prompts/planning.txt` already states, in
plain terms, that "hello" must return tool `"none"` and that single
words which are not file names must too. The model ignored both: it
answered "hello" with read_file("hello.py") and "lpoe" by creating
lpoe.py. Instruction-following on a constrained output format is
exactly what a small model loses first, and exactly what LoRA restores
cheaply.

Why the examples are built rather than collected
------------------------------------------------
The correct output is knowable for a large class of inputs without a
human labelling anything: a greeting maps to `none`, "read X.py" maps
to read_file, and so on. The tool registry supplies the valid names and
argument shapes, so generated pairs are correct by construction rather
than by review.

Format
------
Each line is one JSON object with `prompt` and `completion`. The prompt
is Pearl's REAL planning prompt, rendered through the same template the
planner uses at inference. Training on a paraphrase would teach the
model a format it is never actually shown.

Usage
-----
    python -m finetune.build_dataset --out finetune/data/plans.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Example sources
# ---------------------------------------------------------------------------

_NONE_PLAN = {"steps": [{"tool": "none", "arguments": {}}]}

# Conversational input, in the languages Pearl's greeting gate accepts.
# The deterministic gate catches these before planning, so the model
# never sees them in production — but training on them anyway is what
# stops it inventing work for the near-misses the gate does not catch.
_CONVERSATIONAL = [
    "hello", "hi", "hii", "hey", "yo", "good morning", "good evening",
    "thanks", "thank you", "cheers", "ok", "sure", "got it", "bye",
    "how are you", "who are you", "what can you do", "what is Pearl",
    "namaste", "namaskar", "vanakkam", "shukriya", "dhanyavaad",
    "hola", "gracias", "bonjour", "merci", "danke", "ciao", "obrigado",
    "spasibo", "shukran", "konnichiwa", "arigatou", "xiexie",
    "नमस्ते", "धन्यवाद", "வணக்கம்", "こんにちは", "안녕하세요", "你好",
]

# Single tokens that name nothing. These are the "lpoe" case: the model
# must not invent a file, a search, or anything else for them.
_UNACTIONABLE = [
    "lpoe", "jiii", "asdf", "qwer", "zxcv", "blah", "hmm", "xyz",
    "abcd", "test123", "foo", "aaaa", "kiii", "loo", "poi",
]

_READ_TARGETS = [
    "hello.py", "main.py", "src/utils.py", "config.yaml", "README.md",
    "tests/test_auth.py", "app.js", "server.go", "index.html",
]

_LIST_TARGETS = ["src", "tests", ".", "docs", "src/agent", "scripts"]

_SEARCH_TERMS = [
    "authenticate", "parse_config", "TODO", "def main", "class User",
    "import json", "api_key", "retry",
]

_CREATE_TARGETS = [
    ("notes.txt", "hello world"),
    ("hello.py", "print('hello')"),
    ("data.json", "{}"),
    ("README.md", "# Project"),
]


def _read_examples() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in _READ_TARGETS:
        plan = {"steps": [{"tool": "read_file", "arguments": {"path": path}}]}
        for phrasing in (
            f"read {path}",
            f"show me {path}",
            f"open {path}",
            f"what is in {path}",
        ):
            yield phrasing, plan


def _list_examples() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in _LIST_TARGETS:
        plan = {"steps": [{"tool": "list_directory", "arguments": {"path": path}}]}
        for phrasing in (
            f"list the {path} folder",
            f"what files are in {path}",
            f"show the contents of {path}",
        ):
            yield phrasing, plan


def _search_examples() -> Iterator[tuple[str, dict[str, Any]]]:
    for term in _SEARCH_TERMS:
        plan = {"steps": [{"tool": "search_text", "arguments": {"query": term}}]}
        for phrasing in (
            f"search for {term}",
            f"find {term}",
            f"where is {term}",
        ):
            yield phrasing, plan


def _create_examples() -> Iterator[tuple[str, dict[str, Any]]]:
    for path, content in _CREATE_TARGETS:
        plan = {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": path, "content": content},
                }
            ]
        }
        yield f"create a file called {path} containing: {content}", plan
        yield f"make {path} with the content {content}", plan


def _multi_step_examples() -> Iterator[tuple[str, dict[str, Any]]]:
    """
    Read-before-write, expressed with depends_on.

    This is the shape the plan validator enforces and the model most
    often gets wrong — either writing without reading, or emitting a
    depends_on that names a tool instead of a step id, which is what
    produced the dependency-cycle failures.
    """
    for path in ("src/utils.py", "main.py", "app.js"):
        yield (
            f"add a docstring to the top of {path}",
            {
                "steps": [
                    {
                        "tool": "read_file",
                        "arguments": {"path": path},
                        "id": "read",
                    },
                    {
                        "tool": "replace_in_file",
                        "arguments": {
                            "path": path,
                            "old": "<existing first line>",
                            "new": '"""Module docstring."""',
                        },
                        "id": "write",
                        "depends_on": ["read"],
                    },
                ]
            },
        )


def _repo_question_examples() -> Iterator[tuple[str, dict[str, Any]]]:
    """
    Search before read, for a path the model cannot know.

    The prompt requires this and the model routinely skips it, guessing
    a filename instead — the direct cause of the failed read in the
    "lpoe" trace.
    """
    for thing in ("the login handler", "the retry logic", "the User class"):
        yield (
            f"where is {thing} defined",
            {
                "steps": [
                    {
                        "tool": "search_code",
                        "arguments": {"query": thing},
                    }
                ]
            },
        )


def _test_examples() -> Iterator[tuple[str, dict[str, Any]]]:
    plan = {"steps": [{"tool": "run_tests", "arguments": {"path": "."}}]}
    for phrasing in ("run the tests", "run tests", "execute the test suite"):
        yield phrasing, plan


def collect_examples() -> list[tuple[str, dict[str, Any]]]:
    """Every (user_request, correct_plan) pair, deduplicated."""
    examples: list[tuple[str, dict[str, Any]]] = []

    for text in _CONVERSATIONAL:
        examples.append((text, _NONE_PLAN))
    for text in _UNACTIONABLE:
        # Same target as a greeting: do nothing. The distinction between
        # "friendly" and "meaningless" matters to the UI, not the plan.
        examples.append((text, _NONE_PLAN))

    for source in (
        _read_examples, _list_examples, _search_examples,
        _create_examples, _multi_step_examples,
        _repo_question_examples, _test_examples,
    ):
        examples.extend(source())

    seen: set[str] = set()
    unique: list[tuple[str, dict[str, Any]]] = []
    for text, plan in examples:
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        unique.append((text, plan))
    return unique


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_prompt(user_request: str, workspace: str) -> str:
    """
    Render Pearl's real planning prompt for `user_request`.

    Uses the planner's own template and tool list rather than a
    paraphrase: a model fine-tuned on a prompt it is never shown at
    inference learns the wrong mapping.

    `build_prompt` reads the workspace root from thread-local state
    rather than taking it as an argument, so it is set here and restored
    afterwards — otherwise every rendered prompt would silently carry
    whatever directory the generator happened to run from, and the
    trained model would learn paths from the wrong machine.
    """
    from pathlib import Path

    from src.agent.dispatcher import ToolDispatcher
    from src.agent.planner import Planner
    from src.config import workspace as workspace_state
    from src.main import build_registry

    # Restore the *unset* state, not a materialised fallback.
    #
    # get_workspace_root() returns Path.cwd() when nothing is set, so
    # saving and re-setting its return value would leave the thread-local
    # explicitly pinned where it had been unset — silently changing
    # behaviour for every later caller that relied on the cwd fallback.
    had_root = hasattr(workspace_state._local, "root")
    previous = getattr(workspace_state._local, "root", None)

    workspace_state.set_workspace_root(Path(workspace))
    try:
        registry = build_registry()
        planner = Planner(registry, ToolDispatcher(registry))
        return planner.build_prompt(user_request, workspace_context="")
    finally:
        if had_root:
            workspace_state.set_workspace_root(previous)
        else:
            workspace_state.clear_workspace_root()


def build(out_path: Path, workspace: str, seed: int = 0) -> int:
    examples = collect_examples()
    random.Random(seed).shuffle(examples)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for user_request, plan in examples:
            record = {
                "prompt": render_prompt(user_request, workspace),
                "completion": json.dumps(plan, separators=(",", ":")),
                # Kept for inspection; training reads prompt/completion only.
                "user_request": user_request,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return len(examples)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a planning fine-tune dataset for Pearl."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("finetune/data/plans.jsonl"),
        help="output JSONL path",
    )
    parser.add_argument(
        "--workspace",
        default="/workspace",
        help="workspace root written into the prompt",
    )
    args = parser.parse_args()

    count = build(args.out, args.workspace)
    print(f"wrote {count} examples to {args.out}")

    # Small datasets overfit; say so rather than letting the number look
    # sufficient on its own.
    if count < 500:
        print(
            f"\nNote: {count} examples is a starting point, not a finished "
            "dataset. Expect real gains from a few thousand, and add pairs "
            "from your own repositories — this generator only covers the "
            "shapes Pearl can derive without human labelling."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
