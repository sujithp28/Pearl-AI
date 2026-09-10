"""
How the context ranker scores a file's symbol matches.

Two kinds of test here, doing different jobs.

`TestSymbolScore` pins the properties of the scoring function itself.
These are deterministic, depend on nothing outside the function, and are
the tests that should be read to understand what the scoring guarantees.

`TestRankingOnThisRepository` is an evaluation, not a unit test. It asks
Pearl fourteen questions about its own source and checks how often the
file a maintainer would actually open reaches the model. It therefore
depends on this repository's layout, and it asserts floors rather than
exact ranks so an unrelated rename does not fail it. Its job is to stop
retrieval quality regressing silently, which is how it got bad in the
first place: nobody was measuring it.

The floors are the measured result at the time of writing, not targets.
Raising them requires re-measuring, and lowering one should be a
deliberate, explained decision.
"""

from __future__ import annotations

import pytest

from src.repository.context import (
    SemanticContextBuilder,
    _symbol_score,
    _tokenize,
)
from src.repository.service import RepositoryService

# ---------------------------------------------------------------------------
# The scoring function
# ---------------------------------------------------------------------------


class TestSymbolScore:
    def test_a_single_full_density_match_scores_its_tier_weight(self) -> None:
        """The one-match case is unchanged from simple accumulation."""
        assert _symbol_score({4.0: 1}, 1) == pytest.approx(4.0)

    def test_matches_saturate_rather_than_accumulate(self) -> None:
        """
        Twenty matches must not score twenty times one match.

        This is the whole defect. Scoring each match separately made a
        file's score track how many symbols it contains rather than how
        relevant it is, and a well-named test suite would then outrank
        the source it exercises.
        """
        one = _symbol_score({4.0: 1}, 1)
        twenty = _symbol_score({4.0: 20}, 20)

        assert twenty < one * 5
        assert twenty > one

    def test_score_keeps_growing_with_more_matches(self) -> None:
        """Saturating is not the same as capping: more is still more."""
        scores = [_symbol_score({4.0: n}, n) for n in (1, 5, 20, 100)]

        assert scores == sorted(scores)

    def test_the_same_matches_score_lower_in_a_larger_file(self) -> None:
        """
        Twenty matches out of twenty-five symbols is a file about the
        subject. Twenty out of four hundred is a file that mentions it.
        """
        focused = _symbol_score({4.0: 20}, 25)
        diffuse = _symbol_score({4.0: 20}, 400)

        assert diffuse < focused

    def test_a_diffuse_file_can_score_below_one_precise_match(self) -> None:
        assert _symbol_score({4.0: 20}, 400) < _symbol_score({4.0: 1}, 1)

    def test_tiers_are_summed(self) -> None:
        both = _symbol_score({4.0: 1, 3.0: 1}, 2)

        assert both > _symbol_score({4.0: 1}, 2)

    def test_no_matches_scores_zero(self) -> None:
        assert _symbol_score({}, 10) == 0.0

    def test_zero_symbol_count_does_not_divide_by_zero(self) -> None:
        """A file with matches but no counted symbols must not explode."""
        assert _symbol_score({4.0: 1}, 0) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Retrieval quality on this repository
# ---------------------------------------------------------------------------

# (question, the file(s) a maintainer would open to answer it)
PROBES: list[tuple[str, list[str]]] = [
    (
        "where is the approval gate enforced before a write reaches disk",
        ["src/tools/patch_manager.py", "src/tools/edit_tools.py"],
    ),
    (
        "how does the executor decide to replan after a step fails",
        ["src/agent/executor.py"],
    ),
    ("how are checkpoints stored and restored", ["src/tools/checkpoints.py"]),
    ("what validates a plan before it is executed", ["src/agent/plan_validator.py"]),
    ("how does the MCP server dispatch incoming methods", ["src/mcp/server.py"]),
    ("where are the HTTP endpoints defined", ["src/api/server.py"]),
    ("how are per-user sessions kept separate", ["src/api/tenancy.py"]),
    (
        "how is the LLM provider chosen at startup",
        ["src/llm/router.py", "src/llm/providers/factory.py"],
    ),
    (
        "how does Pearl shrink the conversation when it runs out of context",
        ["src/agent/condenser.py"],
    ),
    ("what computes the confidence score for a plan step", ["src/agent/confidence.py"]),
    ("how does inline code completion work", ["src/agent/completion.py"]),
    (
        "how is the repository index built from source files",
        ["src/repository/index.py", "src/repository/scanner.py"],
    ),
    (
        "how does reflection decide whether the task is complete",
        ["src/agent/reflection.py"],
    ),
    (
        "where are shell commands approved before they run",
        ["src/tools/command_approval.py"],
    ),
]

# Measured floors, not aspirations. See the module docstring.
MIN_IN_TOP_THREE = 7
MIN_SELECTED_AT_ALL = 9


def _rank_for(prompt: str) -> list[str]:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    service = RepositoryService.get_or_build(root)
    builder = SemanticContextBuilder()
    candidates = builder._rank(
        prompt, _tokenize(prompt), service.index, service.graph, None, service.root
    )
    return [str(c.path).replace("\\", "/") for c in candidates]


def _position_of(selected: list[str], wanted: list[str]) -> int | None:
    for i, path in enumerate(selected, 1):
        if any(path.endswith(w) for w in wanted):
            return i
    return None


@pytest.fixture(scope="module")
def outcomes() -> list[int | None]:
    """Where the wanted file landed for each probe. Ranked once."""
    return [_position_of(_rank_for(prompt), wanted) for prompt, wanted in PROBES]


class TestRankingOnThisRepository:
    def test_wanted_file_reaches_the_top_three_often_enough(self, outcomes) -> None:
        in_top_three = sum(1 for pos in outcomes if pos is not None and pos <= 3)

        assert in_top_three >= MIN_IN_TOP_THREE, (
            f"only {in_top_three}/{len(PROBES)} questions put the right file in "
            f"the top three; the floor is {MIN_IN_TOP_THREE}"
        )

    def test_wanted_file_is_selected_often_enough(self, outcomes) -> None:
        """
        Reaching the context at all is the weaker bar, and the one that
        matters most: a file that is never selected cannot be read by
        the model no matter how good the rest of the pipeline is.
        """
        selected = sum(1 for pos in outcomes if pos is not None)

        assert selected >= MIN_SELECTED_AT_ALL, (
            f"only {selected}/{len(PROBES)} questions selected the right file "
            f"at all; the floor is {MIN_SELECTED_AT_ALL}"
        )
