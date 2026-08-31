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

from src.agent.dependency_graph import DependencyCycleError, topological_sort
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
    # Linux/macOS system paths
    "/etc/",
    "/sys/",
    "/proc/",
    "/root/",
    "/boot/",
    "/dev/",
    # Windows system paths (forward-slash form the LLM tends to use)
    "C:/Windows/",
    "C:/Program Files/",
    "C:/Program Files (x86)/",
    # Windows system paths (backslash form)
    "C:\\Windows\\",
    "C:\\Program Files\\",
    "C:\\Program Files (x86)\\",
    # Windows registry (sometimes appears in plans)
    "HKEY_",
    "HKLM\\",
    "HKCU\\",
)

# Tools that destructively modify file content require a read_file step
# to appear earlier in the same plan.  create_file is intentionally
# excluded — it brings a file into existence from scratch.
DESTRUCTIVE_WRITE_TOOLS: frozenset[str] = frozenset(
    (
        "write_file",
        "replace_in_file",
        "edit_lines",
        "patch_file",
    )
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

    # Read-before-write: any destructive write tool must be preceded by at
    # least one read_file step in the plan.  This catches the common
    # planning mistake of modifying a file Pearl has never observed in the
    # current plan.
    has_read = False
    for i, step in enumerate(steps, 1):
        if step.tool_name == "read_file":
            has_read = True
        elif step.tool_name in DESTRUCTIVE_WRITE_TOOLS and not has_read:
            errors.append(
                f"Step {i} ({step.tool_name!r}) modifies a file without a "
                "prior read_file step in this plan."
            )

    if errors:
        raise PlanValidationError("; ".join(errors))

    # Dependency validation — only runs when at least one step carries
    # annotations; plans without any annotations skip this entirely so
    # the cost is zero for the common case.
    if any(s.step_id is not None or s.depends_on for s in steps):
        validate_dependencies(steps)


def validate_dependencies(steps: list[ToolCall]) -> None:
    """
    Validate dependency annotations on a plan.

    Checks applied, in order:

    1. No duplicate step IDs — two steps with the same id would make
       dependency references ambiguous.
    2. No self-dependencies — a step that lists its own id in depends_on
       would create a trivial cycle.
    3. Every depends_on entry references a step id that actually exists
       in this plan.
    4. No dependency cycles — detected by running topological_sort() and
       catching DependencyCycleError.

    Phases 1–3 collect all errors before raising; phase 4 adds cycle
    errors afterward. This ensures the caller sees every structural
    problem in one shot rather than one per call.

    Raises
    ------
    PlanValidationError
        When any of the above checks fail.
    """

    errors: list[str] = []

    # --- Phase 1: collect step IDs, detect duplicates -------------------
    seen_ids: dict[str, int] = {}  # id → 1-indexed step number of first occurrence

    for i, step in enumerate(steps, 1):
        if step.step_id is None:
            continue
        if step.step_id in seen_ids:
            errors.append(
                f"Step {i} has duplicate step_id {step.step_id!r} "
                f"(first seen at step {seen_ids[step.step_id]})."
            )
        else:
            seen_ids[step.step_id] = i

    # --- Phase 2 + 3: validate depends_on entries -----------------------
    for i, step in enumerate(steps, 1):
        for dep in step.depends_on:
            if dep == step.step_id:
                errors.append(
                    f"Step {i} ({step.step_id!r}) depends on itself."
                )
            elif dep not in seen_ids:
                errors.append(
                    f"Step {i} depends on unknown step_id {dep!r}."
                )

    if errors:
        raise PlanValidationError("; ".join(errors))

    # --- Phase 4: cycle detection via topological sort ------------------
    try:
        topological_sort(steps)
    except DependencyCycleError as exc:
        raise PlanValidationError(str(exc)) from exc
