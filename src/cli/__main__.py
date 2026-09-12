"""
Pearl CLI — the V2 autonomous loop in a terminal.

    python -m src.cli                          interactive session
    python -m src.cli "fix the login bug"      one-shot task
    python -m src.cli --chat "what is X?"      chat, no tools
    python -m src.cli --json "task"            machine-readable output
    python -m src.cli --yes "task"             headless (see below)

Runs the same pipeline as the web UI and the VS Code extension:

    PLAN -> EXECUTE -> STAGE -> APPROVE -> VERIFY -> REFLECT -> REPLAN/DONE

Approval
--------
Nothing is written to disk without approval, exactly as everywhere else
in Pearl.  ``--yes`` does not bypass that gate: it routes the decision
through ``ExecutionPolicy``, which still refuses to auto-approve
dangerous operations.  There is deliberately no flag that skips the
gate entirely.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from src.cli import render

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REJECTED = 2
EXIT_BLOCKED = 3
EXIT_INTERRUPTED = 130


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _build_executor(workspace: Path, quiet: bool):
    """
    Assemble the real V2 executor: planner, dispatcher, verifier and
    reflection engine, all wired the same way the API session wires them.
    """
    from src.agent.dispatcher import ToolDispatcher
    from src.agent.executor import AutonomousExecutor
    from src.agent.planner import Planner
    from src.agent.reflection import ReflectionEngine
    from src.agent.verification import VerificationEngine
    from src.llm.router import ModelRouter
    from src.main import build_registry

    router = ModelRouter()
    registry = build_registry()
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher, router.planning_client())

    def _on_progress(event: Any) -> None:
        if quiet:
            return
        detail = getattr(event, "current_action", "") or ""
        total = getattr(event, "total_steps", 0)
        current = getattr(event, "current_step", 0)
        if total:
            detail = f"{detail} [{current}/{total}]".strip()
        print(render.stage(getattr(event, "status", "?"), detail), flush=True)

    executor = AutonomousExecutor(
        planner,
        dispatcher,
        on_progress=_on_progress,
        verifier=VerificationEngine(workspace_root=workspace),
        reflection_engine=ReflectionEngine(router.reflection_client()),
    )
    return executor, router


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def _show_pending(executor: Any) -> None:
    files = executor.patch_manager.affected_files()
    commands = [
        getattr(c, "command", str(c))
        for c in getattr(executor.command_approver, "pending", [])
    ]

    print(render.header("Proposed changes"))
    for f in files:
        print(f"  {render.bold(f)}")
    for c in commands:
        print(f"  {render.yellow('$ ' + str(c))}")

    diff_text = executor.patch_manager.combined_diff()
    if diff_text.strip():
        print()
        print(render.diff(diff_text))


def _decide(executor: Any, auto_yes: bool) -> bool:
    """
    Return whether to apply the staged changes.

    With ``--yes`` the decision goes through ExecutionPolicy rather than
    being assumed — dangerous operations are still refused, so the flag
    cannot be used to sidestep the approval guarantee.

    The risk level is read off the batch that is actually staged, not
    assumed to be ``"staged"``. Passing that constant meant a run whose
    batch contained a `delete_file` — declared ``"dangerous"`` — was
    auto-approved anyway, deleting the file while printing that
    dangerous operations are still refused.
    """
    if auto_yes:
        from src.agent.headless import get_execution_policy

        policy = get_execution_policy()

        # Ask what was actually staged rather than assuming it was an
        # ordinary edit. This used to pass a hardcoded "staged", so a run
        # that staged a deletion — a dangerous tool — was auto-approved in
        # headless mode under the staged policy. Dangerous operations are
        # exactly what --yes must not wave through.
        risk = executor.staged_risk_level()
        allowed = policy.can_auto_approve(risk)
        print()
        if allowed:
            print(render.dim(f"  auto-approved — {policy.approval_reason(risk)}"))
        else:
            print(render.yellow(f"  blocked — {policy.approval_reason(risk)}"))
            if risk == "dangerous":
                print(
                    render.dim(
                        "  this batch deletes files; approve it interactively "
                        "(without --yes) if that is intended"
                    )
                )
            else:
                print(
                    render.dim(
                        "  set PEARL_EXECUTION_MODE=headless and "
                        "PEARL_HEADLESS_STAGED_POLICY=approve to allow this"
                    )
                )
        return allowed

    try:
        answer = (
            input(f"\n{render.bold('Apply these changes?')} [y/N] ").strip().lower()
        )
    except EOFError:
        # Piped stdin with no answer available: refuse rather than assume
        # consent for a filesystem write.
        print(render.dim("\n  no input available — treating as reject"))
        return False
    return answer in {"y", "yes"}


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------


def _run_task(
    prompt: str,
    workspace: Path,
    auto_yes: bool,
    as_json: bool,
) -> int:
    executor, _router = _build_executor(workspace, quiet=as_json)

    if not as_json:
        print(render.header(f"Task: {prompt}"))

    try:
        report = executor.run(prompt)

        while report.stop_reason == "awaiting_approval":
            if not as_json:
                _show_pending(executor)
            if _decide(executor, auto_yes):
                report = executor.approve()
            else:
                report = executor.reject()
                break

    except KeyboardInterrupt:
        executor.cancel()
        print(render.yellow("\n  cancelled — discarding staged changes"))
        return EXIT_INTERRUPTED

    verification = getattr(executor, "_last_verification", None)

    if as_json:
        print(json.dumps(_report_json(report, verification), indent=2))
    else:
        print(render.report(report, verification))
        print()

    if report.stop_reason == "rejected":
        return EXIT_REJECTED
    if report.stop_reason == "awaiting_approval":
        return EXIT_BLOCKED
    return EXIT_OK if report.succeeded else EXIT_FAILED


def _report_json(report: Any, verification: dict | None) -> dict[str, Any]:
    """Machine-readable result for CI and scripting."""
    refl = getattr(report, "llm_reflection", None)
    return {
        "succeeded": report.succeeded,
        "stop_reason": report.stop_reason,
        "replans_used": report.replans_used,
        "steps": [
            {
                "tool": s.tool_name,
                "succeeded": s.succeeded,
                "summary": s.summary,
                "error": s.error,
            }
            for s in report.steps
        ],
        "verification": verification,
        "reflection": (
            {
                "status": refl.status,
                "confidence": refl.confidence,
                "reason": refl.reason,
                "missing_requirements": refl.missing_requirements,
            }
            if refl is not None
            else None
        ),
    }


def _run_chat(message: str, workspace: Path) -> int:
    """Conversational reply — no tools, no writes."""
    from src.llm.router import ModelRouter
    from src.prompts.system import build_chat_system_prompt

    client = ModelRouter().chat_client()
    print()
    for chunk in client.generate_stream(
        message, system=build_chat_system_prompt(str(workspace))
    ):
        print(chunk, end="", flush=True)
    print("\n")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Interactive session
# ---------------------------------------------------------------------------


def _interactive(workspace: Path, auto_yes: bool) -> int:
    from src.llm.router import ModelRouter

    roles = ModelRouter().describe()
    print(render.header("Pearl"))
    print(f"  workspace  {render.bold(str(workspace))}")
    print(f"  profile    {roles.get('profile', '?')}")
    print(f"  planning   {roles.get('planning', '?')}")
    print(render.dim("\n  '?' for chat mode, 'exit' to quit\n"))

    while True:
        try:
            line = input(render.cyan("pearl> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return EXIT_OK

        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            print("Goodbye.")
            return EXIT_OK

        try:
            if line.startswith("?"):
                _run_chat(line[1:].strip(), workspace)
            else:
                _run_task(line, workspace, auto_yes, as_json=False)
        except Exception as exc:
            print(render.red(f"\nError: {exc}\n"))
            logger.debug("CLI task failed", exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pearl",
        description="Pearl — autonomous coding agent (approval-gated).",
    )
    parser.add_argument("task", nargs="?", help="task to run; omit for interactive")
    parser.add_argument("--workspace", default=".", help="project directory")
    parser.add_argument("--chat", action="store_true", help="chat only, no tools")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="auto-approve via ExecutionPolicy (dangerous ops still blocked)",
    )
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s | %(message)s",
    )

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"Not a directory: {workspace}", file=sys.stderr)
        return EXIT_FAILED

    # Every tool resolves paths against this root, so it must be set
    # before any tool can run.
    from src.config.workspace import set_workspace_root

    set_workspace_root(workspace)

    if args.chat:
        if not args.task:
            print("--chat requires a message", file=sys.stderr)
            return EXIT_FAILED
        return _run_chat(args.task, workspace)

    if args.task:
        return _run_task(args.task, workspace, args.yes, args.json)

    return _interactive(workspace, args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
