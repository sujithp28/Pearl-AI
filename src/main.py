"""
Pearl AI Coding Agent.

Application entry point.
"""

from __future__ import annotations

import logging

from src.agent import PearlAgent
from src.tools.checkpoints import CheckpointError
from src.tools.edit_tools import (
    create_file,
    edit_lines,
    patch_file,
    replace_in_file,
)
from src.tools.file_tools import (
    append_file,
    delete_file,
    file_exists,
    file_size,
    list_directory,
    make_directory,
    read_file,
    write_file,
)
from src.tools.git_tools import (
    git_commit,
    git_create_branch,
    git_diff,
    git_log,
    git_restore,
    git_status,
)
from src.tools.registry import ToolRegistry
from src.tools.repo_tools import (
    explain_file,
    find_references,
    find_symbol,
    index_repository,
    search_text,
    summarize_project,
)
from src.tools.shell_tools import (
    current_user,
    execute_shell,
    is_command_available,
    ls,
    operating_system,
    pwd,
    run_python,
    which,
)
from src.tools.symbol_editor import (
    find_class,
    find_function,
    find_method,
    insert_after_symbol,
    insert_before_symbol,
    replace_class,
    replace_function,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
)


def build_registry() -> ToolRegistry:
    """
    Create the registry and register all available tools.
    """

    registry = ToolRegistry()

    # File tools
    registry.register(read_file)
    registry.register(write_file)
    registry.register(append_file)
    registry.register(file_exists)
    registry.register(list_directory)
    registry.register(make_directory)
    registry.register(delete_file)
    registry.register(file_size)

    # Edit tools
    registry.register(create_file)
    registry.register(replace_in_file)
    registry.register(edit_lines)
    registry.register(patch_file)

    # Repository intelligence tools
    registry.register(index_repository)
    registry.register(find_symbol)
    registry.register(find_references)
    registry.register(search_text)
    registry.register(summarize_project)
    registry.register(explain_file)

    # Shell tools
    registry.register(execute_shell)
    registry.register(run_python)
    registry.register(pwd)
    registry.register(ls)
    registry.register(which)
    registry.register(is_command_available)
    registry.register(operating_system)
    registry.register(current_user)

    # Git tools
    registry.register(git_diff)
    registry.register(git_status)
    registry.register(git_log)
    registry.register(git_create_branch)
    registry.register(git_commit)
    registry.register(git_restore)

    # Symbol-aware editing tools
    registry.register(find_function)
    registry.register(find_class)
    registry.register(find_method)
    registry.register(replace_function)
    registry.register(replace_class)
    registry.register(insert_after_symbol)
    registry.register(insert_before_symbol)

    return registry


def _print_report(report) -> None:
    """
    Summarize an `ExecutionReport` for the terminal.
    """

    for step in report.steps:
        marker = "OK" if step.succeeded else "FAIL"
        print(f"  [{marker}] {step.tool_name}: {step.summary}")

    print(f"\nStop reason: {report.stop_reason}")


def _print_checkpoints(agent: PearlAgent) -> None:
    checkpoints = agent.checkpoints.list()

    if not checkpoints:
        print("No checkpoints yet.")
        return

    for checkpoint in checkpoints:
        print(f"  {checkpoint.short_id}  {checkpoint.created_at}  {checkpoint.label}")


def _handle_checkpoint_command(agent: PearlAgent, command: str) -> bool:
    """
    Handle a `:`-prefixed checkpoint command. Returns whether
    `command` was recognized as one (so the caller knows not to also
    treat it as a natural-language prompt).

    Kept to the `:` prefix specifically so a plain-English request
    that happens to start with a word like "list" or "restore" is
    never mistaken for a command — the parser only ever looks at
    `:`-prefixed input.
    """

    head, _, rest = command[1:].partition(" ")
    action = head.lower()
    rest = rest.strip()

    if not action:
        return False

    try:
        if action == "checkpoints":
            _print_checkpoints(agent)
            return True

        if action == "checkpoint":
            # Everything after "checkpoint " is the label, spaces and
            # all — not just its first word.
            label = rest or "Manual checkpoint"
            checkpoint = agent.checkpoints.create(label)

            if checkpoint is None:
                print("Nothing has changed since the last checkpoint.")
            else:
                print(f"Checkpoint {checkpoint.short_id} saved: {checkpoint.label}")

            return True

        if action == "restore":
            if not rest:
                print("Usage: :restore <checkpoint-id>")
                return True

            checkpoint_id = rest.split(maxsplit=1)[0]
            preview = agent.checkpoints.preview_restore(checkpoint_id)

            if not preview.changed_anything:
                print("Nothing to restore — already matches this checkpoint.")
                return True

            print("Restoring will:")
            for path in preview.restored:
                print(f"  revert   {path}")
            for path in preview.removed:
                print(f"  remove   {path}")

            answer = input("Proceed? [y/N] ").strip().lower()

            if answer in {"y", "yes"}:
                agent.checkpoints.restore(checkpoint_id)
                print("Restored.")
            else:
                print("Cancelled.")

            return True

        if action == "delete":
            if not rest:
                print("Usage: :delete <checkpoint-id>")
                return True

            checkpoint_id = rest.split(maxsplit=1)[0]
            agent.checkpoints.delete(checkpoint_id)
            print("Deleted.")
            return True

        if action == "rename":
            checkpoint_id, _, new_label = rest.partition(" ")
            new_label = new_label.strip()

            if not checkpoint_id or not new_label:
                print("Usage: :rename <checkpoint-id> <new label>")
                return True

            checkpoint = agent.checkpoints.rename(checkpoint_id, new_label)
            print(f"Renamed to: {checkpoint.label}")
            return True

    except CheckpointError as exc:
        print(f"Error: {exc}")
        return True

    if action in {"checkpoints", "checkpoint", "restore", "delete", "rename"}:
        # Reachable only if a branch above didn't already return —
        # kept as a safety net so a future new action isn't silently
        # sent to the LLM as a natural-language prompt if a return is
        # ever missed.
        return True

    print(
        f"Unknown command ':{action}'. Try :checkpoints, :checkpoint, "
        ":restore, :delete, or :rename."
    )
    return True


def _run_and_resolve(agent: PearlAgent, prompt: str) -> None:
    """
    Run `prompt` autonomously, then — if it pauses for a staged patch
    or command — prompt the user to approve or reject before
    returning, so the CLI never leaves a run stuck awaiting approval
    with no way to resolve it.
    """

    report = agent.run_autonomous(prompt)

    while report.stop_reason == "awaiting_approval":
        _print_report(report)
        print("\nChanges are staged and awaiting approval.")

        answer = input("Apply these changes? [y/N] ").strip().lower()

        if answer in {"y", "yes"}:
            report = agent.approve()
        else:
            report = agent.reject()

    print()
    _print_report(report)
    print()


def main() -> None:
    """
    Start Pearl.
    """

    print("=" * 60)
    print(" Pearl AI Coding Agent")
    print("=" * 60)

    registry = build_registry()

    agent = PearlAgent(registry)

    print(f"Loaded {len(registry)} tools.")
    print("Type 'exit' to quit.")
    print(
        "Checkpoints: :checkpoints | :checkpoint [label] | :restore <id> | "
        ":delete <id> | :rename <id> <label>\n"
    )

    while True:
        try:
            prompt = input("Pearl > ").strip()

            if not prompt:
                continue

            if prompt.lower() in {
                "exit",
                "quit",
            }:
                print("Goodbye.")
                break

            if prompt.startswith(":"):
                if _handle_checkpoint_command(agent, prompt):
                    continue

            _run_and_resolve(agent, prompt)

        except KeyboardInterrupt:
            print("\nGoodbye.")
            break

        except Exception as exc:
            print(f"\nError: {exc}\n")


if __name__ == "__main__":
    main()
