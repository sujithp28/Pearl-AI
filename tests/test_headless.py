"""
Tests for src/agent/headless.py — ExecutionPolicy
"""
from __future__ import annotations

import pytest

from src.agent.headless import ExecutionPolicy, get_execution_policy


def _policy(mode: str, staged_policy: str = "block") -> ExecutionPolicy:
    return ExecutionPolicy(mode=mode, staged_policy=staged_policy)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Interactive mode — nothing auto-approved
# ---------------------------------------------------------------------------


class TestInteractiveMode:
    def test_safe_not_auto_approved(self) -> None:
        assert _policy("interactive").can_auto_approve("safe") is False

    def test_staged_not_auto_approved(self) -> None:
        assert _policy("interactive").can_auto_approve("staged") is False

    def test_dangerous_not_auto_approved(self) -> None:
        assert _policy("interactive").can_auto_approve("dangerous") is False

    def test_is_interactive_true(self) -> None:
        assert _policy("interactive").is_interactive is True

    def test_is_headless_false(self) -> None:
        assert _policy("interactive").is_headless is False


# ---------------------------------------------------------------------------
# Headless mode — safe always, staged follows policy
# ---------------------------------------------------------------------------


class TestHeadlessMode:
    def test_safe_auto_approved(self) -> None:
        assert _policy("headless").can_auto_approve("safe") is True

    def test_staged_approved_when_policy_approve(self) -> None:
        policy = _policy("headless", staged_policy="approve")
        assert policy.can_auto_approve("staged") is True

    def test_staged_blocked_when_policy_block(self) -> None:
        policy = _policy("headless", staged_policy="block")
        assert policy.can_auto_approve("staged") is False

    def test_dangerous_never_auto_approved(self) -> None:
        # Even with permissive staged policy, dangerous is always blocked.
        policy = _policy("headless", staged_policy="approve")
        assert policy.can_auto_approve("dangerous") is False

    def test_is_interactive_false(self) -> None:
        assert _policy("headless").is_interactive is False

    def test_is_headless_true(self) -> None:
        assert _policy("headless").is_headless is True


# ---------------------------------------------------------------------------
# CI mode — safe always, staged ALWAYS blocked
# ---------------------------------------------------------------------------


class TestCIMode:
    def test_safe_auto_approved(self) -> None:
        assert _policy("ci").can_auto_approve("safe") is True

    def test_staged_always_blocked_in_ci(self) -> None:
        # Even if staged_policy is "approve", CI always blocks staged.
        policy = _policy("ci", staged_policy="approve")
        assert policy.can_auto_approve("staged") is False

    def test_dangerous_never_auto_approved(self) -> None:
        assert _policy("ci").can_auto_approve("dangerous") is False

    def test_is_interactive_false(self) -> None:
        assert _policy("ci").is_interactive is False

    def test_is_headless_true(self) -> None:
        assert _policy("ci").is_headless is True


# ---------------------------------------------------------------------------
# Unknown mode — falls back to interactive (fail closed)
# ---------------------------------------------------------------------------


class TestUnknownMode:
    def test_unknown_mode_falls_back_to_interactive(self) -> None:
        policy = _policy("unknown_xyz")
        # Falls back to interactive: nothing auto-approved
        assert policy.can_auto_approve("safe") is False
        assert policy.can_auto_approve("staged") is False

    def test_mode_stored_as_interactive_after_fallback(self) -> None:
        policy = _policy("totally_wrong")
        assert policy.mode == "interactive"


# ---------------------------------------------------------------------------
# approval_reason messages
# ---------------------------------------------------------------------------


class TestApprovalReason:
    def test_dangerous_always_mentions_dangerous(self) -> None:
        for mode in ("interactive", "headless", "ci"):
            reason = _policy(mode).approval_reason("dangerous")
            assert "dangerous" in reason.lower() or "explicit" in reason.lower()

    def test_interactive_mentions_interactive(self) -> None:
        reason = _policy("interactive").approval_reason("safe")
        assert "interactive" in reason.lower()

    def test_safe_in_headless_mentions_auto(self) -> None:
        reason = _policy("headless").approval_reason("safe")
        assert "auto" in reason.lower()

    def test_staged_blocked_reason_mentions_block(self) -> None:
        reason = _policy("headless", staged_policy="block").approval_reason("staged")
        assert "block" in reason.lower()

    def test_staged_approved_reason_mentions_auto(self) -> None:
        reason = _policy("headless", staged_policy="approve").approval_reason("staged")
        assert "auto" in reason.lower()


# ---------------------------------------------------------------------------
# get_execution_policy reads from Settings
# ---------------------------------------------------------------------------


class TestGetExecutionPolicy:
    def test_returns_execution_policy_instance(self, monkeypatch) -> None:
        monkeypatch.setenv("PEARL_EXECUTION_MODE", "interactive")
        monkeypatch.setenv("PEARL_HEADLESS_STAGED_POLICY", "block")
        # Need to reload settings to pick up monkeypatch
        from src.config.settings import Settings
        monkeypatch.setattr(Settings, "EXECUTION_MODE", "interactive")
        monkeypatch.setattr(Settings, "HEADLESS_STAGED_POLICY", "block")
        policy = get_execution_policy()
        assert isinstance(policy, ExecutionPolicy)

    def test_headless_env_produces_headless_policy(self, monkeypatch) -> None:
        from src.config.settings import Settings
        monkeypatch.setattr(Settings, "EXECUTION_MODE", "headless")
        monkeypatch.setattr(Settings, "HEADLESS_STAGED_POLICY", "approve")
        policy = get_execution_policy()
        assert policy.is_headless is True
        assert policy.can_auto_approve("staged") is True
