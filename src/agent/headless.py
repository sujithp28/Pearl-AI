"""
Headless / CI execution mode for Pearl V2.

Provides a policy layer that determines whether tool operations can run
without interactive approval, based on PEARL_EXECUTION_MODE.

Modes:
  interactive (default): Normal Pearl behavior — all writes wait for approval.
  headless: Safe tools auto-approved, staged tools follow HEADLESS_STAGED_POLICY.
  ci: Safe tools auto-approved, staged tools blocked unless explicitly configured.

Security guarantees:
  - Dangerous tools are ALWAYS blocked in headless/CI mode.
  - The ChangeManager approval gate remains active; this module only
    decides whether to auto-approve it after the run.
  - Fail closed: if mode is unknown, treat as interactive (most restrictive).

Usage::

    policy = get_execution_policy()
    if policy.can_auto_approve(tool_risk_level):
        # skip interactive approval
    else:
        # surface approval to user / block
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from src.config.settings import Settings
from src.tools.models import RiskLevel

logger = logging.getLogger(__name__)

ExecutionMode = Literal["interactive", "headless", "ci"]
StagedPolicy = Literal["approve", "block"]


class ExecutionPolicy:
    """
    Determines approval behavior based on execution mode.

    This class encodes the approval policy — it does NOT execute tools or
    bypass ChangeManager.  It is consulted by the approval coordinator to
    decide whether to automatically approve a staged patch set.
    """

    def __init__(self, mode: ExecutionMode, staged_policy: StagedPolicy) -> None:
        if mode not in ("interactive", "headless", "ci"):
            logger.warning(
                "Unknown PEARL_EXECUTION_MODE %r — falling back to 'interactive'", mode
            )
            mode = "interactive"
        self.mode = mode
        self.staged_policy = staged_policy

    @property
    def is_interactive(self) -> bool:
        return self.mode == "interactive"

    @property
    def is_headless(self) -> bool:
        return self.mode in ("headless", "ci")

    def can_auto_approve(self, risk_level: RiskLevel) -> bool:
        """
        Return True if a tool at `risk_level` may skip interactive approval.

        Dangerous tools are ALWAYS blocked from auto-approval.
        """
        if risk_level == "dangerous":
            return False

        if self.mode == "interactive":
            return False

        if risk_level == "safe":
            return True  # read-only: always auto-approve in headless/CI

        # risk_level == "staged"
        if self.mode == "ci":
            return False  # CI: block staged writes by default

        # headless mode: follow HEADLESS_STAGED_POLICY
        return self.staged_policy == "approve"

    def approval_reason(self, risk_level: RiskLevel) -> str:
        """Return a human-readable explanation of the approval decision."""
        if risk_level == "dangerous":
            return "Dangerous operations require explicit user confirmation."
        if self.mode == "interactive":
            return "Interactive mode: all writes require user approval."
        if risk_level == "safe":
            return f"Auto-approved: read-only tool in {self.mode} mode."
        if self.mode == "ci":
            return "CI mode: staged writes blocked unless configured."
        return f"Headless mode, staged policy={self.staged_policy!r}: " + (
            "auto-approved." if self.staged_policy == "approve" else "blocked."
        )


def staged_batch_risk_level(
    patch_manager: Any,
    command_approver: Any = None,
) -> RiskLevel:
    """
    Return the highest risk level present in a staged batch.

    A batch is approved as a whole, so the decision has to be made
    against its most dangerous member. Asking
    ``can_auto_approve("staged")`` for every batch -- which is what the
    CLI's ``--yes`` used to do -- auto-approved a staged `delete_file`
    in headless mode, because the *constant* said "staged" while the
    batch actually contained the one operation this module documents as
    always requiring a human.

    A pending deletion is the irreversible case: `delete_file` is
    declared ``risk_level="dangerous"``, and it is the only tool that
    puts a deletion in a `ChangeManager`. Content edits and staged
    commands are ``"staged"``. An empty batch is ``"safe"`` -- there is
    nothing to approve.
    """
    edits = list(getattr(patch_manager, "pending", []) or [])
    commands = list(getattr(command_approver, "pending", []) or [])

    if any(getattr(edit, "is_deletion", False) for edit in edits):
        return "dangerous"

    if edits or commands:
        return "staged"

    return "safe"


def get_execution_policy() -> ExecutionPolicy:
    """Return the active execution policy from Settings."""
    mode = Settings.EXECUTION_MODE
    staged_policy = Settings.HEADLESS_STAGED_POLICY
    policy = ExecutionPolicy(
        mode=mode,  # type: ignore[arg-type]
        staged_policy=staged_policy,  # type: ignore[arg-type]
    )
    logger.debug(
        "ExecutionPolicy: mode=%r staged_policy=%r", policy.mode, policy.staged_policy
    )
    return policy
