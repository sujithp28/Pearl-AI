"""
Deterministic confidence scoring for Pearl plans.

Every score is computed from observable facts about the plan — tool
names, step positions, argument content — with no LLM calls, no
probabilistic models, and no hidden state. Each deduction is named and
described so callers can explain the score to a user.

Design choices:

  • Per-step scoring captures per-step risk (tool type, position).
  • Plan-level factors (e.g. replan history) adjust the overall score
    after step scores are averaged, so the two layers stay independent.
  • Tool classification is name-based because the Tool model carries no
    category metadata. When the Tool model gains a category field this
    can be replaced by a metadata lookup without changing the interface.
  • "Unknown tool" is NOT a confidence factor: by the time this module
    is called, validate_tool_call() has already rejected any plan that
    references an unregistered tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.llm.parser import ToolCall

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

# Per step beyond the first: later steps depend on more prior work.
POSITION_PENALTY: float = 0.02

# The tool writes to the filesystem or repository state.
WRITE_PENALTY: float = 0.10

# The tool executes arbitrary shell or Python code.
SHELL_PENALTY: float = 0.20

# Additional penalty for shell commands that chain operations (pipes,
# redirects, &&, ||, ;) — harder to predict, harder to sandbox.
SHELL_COMPLEX_PENALTY: float = 0.05

# Applies to the overall plan score per prior replan, up to MAX_REPLAN_PENALTY.
REPLAN_PENALTY: float = 0.15
MAX_REPLAN_PENALTY: float = 0.30

# ---------------------------------------------------------------------------
# Tool classification sets
# ---------------------------------------------------------------------------

# Tools that write, modify, or delete filesystem entries or repository state.
# Sourced from the actual tools registered in src/tools/; update here when
# new write-capable tools are added.
WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # edit_tools.py — all staged via PatchManager in autonomous mode
        "create_file",
        "replace_in_file",
        "edit_lines",
        "patch_file",
        # file_tools.py — direct filesystem mutations
        "write_file",
        "append_file",
        "make_directory",
        "delete_file",
        # symbol_editor.py — semantic edits, also staged
        "replace_function",
        "replace_class",
        "insert_after_symbol",
        "insert_before_symbol",
        # git_tools.py — repository state mutations
        "git_commit",
        "git_restore",
        "git_create_branch",
    }
)

# Tools that execute arbitrary code or commands.
SHELL_TOOL_NAMES: frozenset[str] = frozenset({"execute_shell", "run_python"})

# Shell argument substrings that indicate a command chains multiple
# operations — harder to predict and harder to review.
SHELL_COMPLEX_INDICATORS: tuple[str, ...] = (
    "&&",
    "||",
    ";",
    "|",
    ">>",
    ">",
    "<",
    "`",
    "$(",
)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ConfidenceFactor:
    """A single deduction applied to a confidence score, with its rationale."""

    name: str
    deduction: float
    description: str


@dataclass
class StepConfidence:
    """Confidence score for a single plan step."""

    step_index: int                          # 0-based
    tool_name: str
    score: float                             # [0.0, 1.0]
    factors: list[ConfidenceFactor] = field(default_factory=list)


@dataclass
class PlanConfidence:
    """
    Confidence scores for a complete plan.

    ``score``        — overall plan confidence [0.0, 1.0], computed as the
                       mean of per-step scores adjusted by plan-level factors.
    ``step_scores``  — per-step breakdown (same order as the plan).
    ``plan_factors`` — plan-level adjustments applied after step averaging
                       (currently: replan history).
    """

    score: float
    step_scores: list[StepConfidence] = field(default_factory=list)
    plan_factors: list[ConfidenceFactor] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------


def score_plan(
    steps: list[ToolCall],
    *,
    replans_used: int = 0,
) -> PlanConfidence:
    """
    Compute a confidence score for a complete plan.

    ``steps``        — the ordered list of planned tool calls.
    ``replans_used`` — number of replans already consumed before this plan
                       was produced (0 for the initial plan, ≥1 for replans).

    Returns a ``PlanConfidence`` whose ``score`` is [0.0, 1.0] and whose
    ``step_scores`` and ``plan_factors`` explain every deduction.

    If ``steps`` is empty the score is 1.0 with no factors — the empty case
    should have been rejected by ``validate_plan()`` first; this is a
    defensive default.
    """

    if not steps:
        return PlanConfidence(score=1.0)

    step_scores = [_score_step(step, i) for i, step in enumerate(steps)]

    # Start from the mean of per-step scores.
    base = sum(sc.score for sc in step_scores) / len(step_scores)

    plan_factors: list[ConfidenceFactor] = []

    if replans_used > 0:
        deduction = round(min(MAX_REPLAN_PENALTY, REPLAN_PENALTY * replans_used), 4)
        plan_factors.append(
            ConfidenceFactor(
                name="prior_replans",
                deduction=deduction,
                description=(
                    f"{replans_used} replan(s) already used: "
                    "the task is harder than the initial plan anticipated"
                ),
            )
        )
        base -= deduction

    overall = _clamp(base)

    return PlanConfidence(
        score=overall,
        step_scores=step_scores,
        plan_factors=plan_factors,
    )


def _score_step(step: ToolCall, index: int) -> StepConfidence:
    """
    Compute the confidence score for a single plan step.

    Factors:
      position        — later steps depend on more prior steps succeeding.
      write_operation — tool modifies the filesystem or repository state.
      shell_command   — tool executes arbitrary code.
      complex_shell   — shell command chains multiple operations.

    "none" sentinel steps always score 1.0 regardless of position: they
    signal that no action is needed and carry no execution risk.
    """

    if step.tool_name == "none":
        return StepConfidence(step_index=index, tool_name="none", score=1.0)

    score = 1.0
    factors: list[ConfidenceFactor] = []

    if index > 0:
        deduction = round(POSITION_PENALTY * index, 4)
        score -= deduction
        factors.append(
            ConfidenceFactor(
                name="position",
                deduction=deduction,
                description=(
                    f"Step {index + 1} depends on {index} prior step(s) succeeding"
                ),
            )
        )

    if step.tool_name in WRITE_TOOL_NAMES:
        score -= WRITE_PENALTY
        factors.append(
            ConfidenceFactor(
                name="write_operation",
                deduction=WRITE_PENALTY,
                description=f"'{step.tool_name}' modifies files or repository state",
            )
        )

    if step.tool_name in SHELL_TOOL_NAMES:
        score -= SHELL_PENALTY
        factors.append(
            ConfidenceFactor(
                name="shell_command",
                deduction=SHELL_PENALTY,
                description=f"'{step.tool_name}' executes arbitrary code",
            )
        )

        # Only check shell command complexity for execute_shell (the command
        # argument is a shell string); run_python's script argument is Python
        # code and complexity there is harder to assess by string scanning.
        if step.tool_name == "execute_shell":
            command = str(step.kwargs.get("command", ""))
            if any(ind in command for ind in SHELL_COMPLEX_INDICATORS):
                score -= SHELL_COMPLEX_PENALTY
                factors.append(
                    ConfidenceFactor(
                        name="complex_shell",
                        deduction=SHELL_COMPLEX_PENALTY,
                        description=(
                            "Shell command chains multiple operations "
                            "(pipes, redirects, or logical operators)"
                        ),
                    )
                )

    return StepConfidence(
        step_index=index,
        tool_name=step.tool_name,
        score=_clamp(score),
        factors=factors,
    )


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)
