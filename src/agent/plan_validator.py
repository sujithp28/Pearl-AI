"""
Deterministic whole-plan validation for Pearl.

Called by Planner.plan() and Planner.replan() after the LLM returns a
plan and before per-step tool validation or execution begins.

Validates structural properties (step count) and scope (no arguments
that reference forbidden system paths). Raises PlanValidationError on
any failure so the caller fails fast rather than wasting execution
budget on a plan that cannot succeed.

Does NOT duplicate per-step tool validation (that is
validate_tool_call's job) and does NOT re-implement the workspace
boundary check (_ensure_within_workspace does that at tool execution
time). The checks here are pre-execution fast-fails only.
"""

from __future__ import annotations

from src.llm.parser import ToolCall


class PlanValidationError(ValueError):
    """
    Raised when a plan fails structural or scope validation before
    execution begins.

    Subclasses ValueError so existing callers that catch ValueError
    (e.g. Planner.plan() callers) handle it correctly without changes.
    """


MAX_STEPS = 25

# Absolute path prefixes that can never be a legitimate target for a
# coding agent operating inside a developer workspace. Referencing these
# is always either a planning mistake or a prompt-injection attempt.
FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
    "/etc/",
    "/sys/",
    "/proc/",
    "/root/",
    "/boot/",
    "/dev/",
)


def validate_plan(steps: list[ToolCall]) -> None:
    """
    Validate the structure and scope of a complete plan.

    Checks applied, in order:

    1. Step count — zero steps is a structural error (the parser should
       have caught it first, but we are defensive); more than MAX_STEPS
       violates PLAN-4.
    2. Forbidden paths — any string argument that starts with a known
       dangerous system-path prefix is rejected immediately so the
       executor never attempts it.

    Raises
    ------
    PlanValidationError
        Immediately when the plan is empty (nothing else to validate).
        After collecting all errors for non-empty plans, so the caller
        sees every problem at once rather than one at a time.
    """

    if not steps:
        raise PlanValidationError("Plan must contain at least one step.")

    errors: list[str] = []

    if len(steps) > MAX_STEPS:
        errors.append(
            f"Plan has {len(steps)} step(s); maximum is {MAX_STEPS}."
        )

    for i, step in enumerate(steps, 1):
        if step.tool_name == "none":
            continue

        for arg_name, arg_val in step.kwargs.items():
            if not isinstance(arg_val, str):
                continue
            for prefix in FORBIDDEN_PATH_PREFIXES:
                if arg_val.startswith(prefix):
                    errors.append(
                        f"Step {i} ({step.tool_name!r}): argument "
                        f"{arg_name!r} references a forbidden system "
                        f"path: {arg_val!r}"
                    )

    if errors:
        raise PlanValidationError("; ".join(errors))
