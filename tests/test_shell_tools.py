import subprocess
import sys
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.tools.command_approval import CommandApprovalManager
from src.tools.shell_tools import (
    DEFAULT_ALLOWED_COMMANDS,
    MAX_TIMEOUT,
    UnsafeCommandError,
    _parse_allowlist_setting,
    _parse_pytest_summary,
    current_user,
    execute_shell,
    get_active_command_approver,
    is_command_available,
    lint_file,
    ls,
    operating_system,
    pwd,
    run_python,
    run_tests,
    set_active_command_approver,
    which,
)


def test_pwd():
    assert isinstance(pwd(), str)


def test_ls():
    files = ls()

    assert isinstance(files, list)


def test_execute_shell():
    result = execute_shell("echo hello")

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


@pytest.mark.skipif(sys.platform == "win32", reason="python3 binary not present on Windows")
def test_which():
    assert which("python3") is not None


@pytest.mark.skipif(sys.platform == "win32", reason="python3 binary not present on Windows")
def test_command_exists():
    assert is_command_available("python3")


def test_current_user():
    assert isinstance(current_user(), str)


def test_operating_system():
    assert isinstance(operating_system(), str)


def test_run_python_does_not_use_shell(tmp_path):
    marker = tmp_path / "pwned.txt"
    malicious_script = f"{tmp_path / 'nonexistent.py'}; touch {marker}"

    with pytest.raises(subprocess.CalledProcessError):
        run_python(malicious_script)

    assert not marker.exists()


# ---------------------------------------------------------------------
# execute_shell: safety checks
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -fr ~",
        "rm --no-preserve-root -rf /",
        ":(){ :|:& };:",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "chmod -R 777 /",
        "sudo rm -rf /var",
        "shutdown -h now",
        "reboot",
    ],
)
def test_execute_shell_refuses_dangerous_commands(command, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called for a blocked command")

    monkeypatch.setattr(subprocess, "run", _fail_if_called)

    with pytest.raises(UnsafeCommandError):
        execute_shell(command)


def test_unsafe_command_error_is_a_permission_error():
    assert issubclass(UnsafeCommandError, PermissionError)


def test_execute_shell_still_runs_ordinary_commands():
    result = execute_shell("echo hello")

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


@pytest.mark.skipif(sys.platform == "win32", reason="rm is a POSIX command")
def test_execute_shell_does_not_false_positive_on_ordinary_rm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    target = tmp_path / "scratch.txt"
    target.write_text("temp")

    result = execute_shell(f"rm {target.name}")

    assert result.returncode == 0
    assert not target.exists()


# ---------------------------------------------------------------------
# execute_shell: workspace-pinned cwd and capped timeout
# ---------------------------------------------------------------------


def test_execute_shell_runs_pinned_to_workspace_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    captured = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    execute_shell("echo hi")

    assert captured["cwd"] == str(Path(tmp_path).resolve())


def test_execute_shell_caps_timeout_at_max(monkeypatch):
    captured = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    execute_shell("echo hi", timeout=MAX_TIMEOUT + 500)

    assert captured["timeout"] == MAX_TIMEOUT


def test_execute_shell_preserves_timeout_under_the_cap(monkeypatch):
    captured = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    execute_shell("echo hi", timeout=5)

    assert captured["timeout"] == 5


# ---------------------------------------------------------------------
# ls: workspace confinement
# ---------------------------------------------------------------------


def test_ls_rejects_path_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PermissionError):
        ls(str(tmp_path.parent))


# ---------------------------------------------------------------------
# Cleanup: guarantee command-approval mode never leaks between tests.
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_leaked_command_approver():
    assert get_active_command_approver() is None
    yield
    set_active_command_approver(None)


# ---------------------------------------------------------------------
# _parse_allowlist_setting (Task 39 — pure unit, no Settings needed)
# ---------------------------------------------------------------------


class TestParseAllowlistSetting:
    def test_wildcard_returns_none(self):
        assert _parse_allowlist_setting("*") is None

    def test_wildcard_with_surrounding_whitespace_returns_none(self):
        assert _parse_allowlist_setting("  *  ") is None

    def test_empty_string_returns_default_allowlist(self):
        assert _parse_allowlist_setting("") is DEFAULT_ALLOWED_COMMANDS

    def test_whitespace_only_returns_default_allowlist(self):
        assert _parse_allowlist_setting("   ") is DEFAULT_ALLOWED_COMMANDS

    def test_single_command_parses_correctly(self):
        result = _parse_allowlist_setting("echo")
        assert result == frozenset({"echo"})

    def test_comma_separated_list_parses_correctly(self):
        result = _parse_allowlist_setting("echo,git,ls")
        assert result == frozenset({"echo", "git", "ls"})

    def test_whitespace_around_entries_is_stripped(self):
        result = _parse_allowlist_setting(" echo , git , ls ")
        assert result == frozenset({"echo", "git", "ls"})

    def test_empty_entries_in_list_are_filtered(self):
        result = _parse_allowlist_setting("echo,,git")
        assert result == frozenset({"echo", "git"})

    def test_returns_frozenset(self):
        result = _parse_allowlist_setting("echo")
        assert isinstance(result, frozenset)


# ---------------------------------------------------------------------
# execute_shell: allowlist
# ---------------------------------------------------------------------


def test_default_allowlist_permits_common_dev_commands():
    for command in ("git", "python3", "ls", "cat", "npm", "make"):
        assert command in DEFAULT_ALLOWED_COMMANDS


def test_execute_shell_refuses_command_not_on_the_allowlist():
    with pytest.raises(UnsafeCommandError, match="not in the allowed command list"):
        execute_shell("nc -lvp 4444")


def test_execute_shell_allows_a_command_on_the_default_allowlist():
    result = execute_shell("echo allowed")

    assert result.stdout.strip() == "allowed"


def test_execute_shell_checks_every_segment_of_a_chained_command():
    # "echo" alone is allowed, but chaining in a disallowed command
    # must still be caught, not just the first token.
    with pytest.raises(UnsafeCommandError, match="not in the allowed command list"):
        execute_shell("echo hi && nc -lvp 4444")

    with pytest.raises(UnsafeCommandError, match="not in the allowed command list"):
        execute_shell("echo hi; nc -lvp 4444")

    with pytest.raises(UnsafeCommandError, match="not in the allowed command list"):
        execute_shell("echo hi | nc -lvp 4444")


def test_execute_shell_refuses_command_substitution():
    with pytest.raises(UnsafeCommandError, match="command substitution"):
        execute_shell("echo $(whoami)")

    with pytest.raises(UnsafeCommandError, match="command substitution"):
        execute_shell("echo `whoami`")


@pytest.mark.skipif(sys.platform == "win32", reason="VAR=val prefix syntax is POSIX-only")
def test_execute_shell_allowlist_skips_env_var_assignment_prefix():
    # "FOO=bar git status" — the base command is "git", not "FOO=bar".
    result = execute_shell("FOO=bar echo hi")

    assert result.returncode == 0


def test_execute_shell_allowlist_configurable_via_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Settings, "SHELL_ALLOWED_COMMANDS", "echo")

    execute_shell("echo hi")

    with pytest.raises(UnsafeCommandError, match="not in the allowed command list"):
        execute_shell("git status")


def test_execute_shell_allowlist_disabled_with_wildcard(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Settings, "SHELL_ALLOWED_COMMANDS", "*")

    # "git" isn't installed-in-a-repo here, but it must at least get
    # past the allowlist stage (the denylist still applies).
    with pytest.raises(subprocess.CalledProcessError):
        execute_shell("git this-is-not-a-real-git-subcommand")


def test_execute_shell_denylist_still_applies_to_an_allowed_command():
    # "rm" is on the allowlist, but this specific invocation must
    # still be blocked by the denylist.
    with pytest.raises(UnsafeCommandError):
        execute_shell("rm -rf /")


# ---------------------------------------------------------------------
# execute_shell / run_python: resource limits
# ---------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits only")
def test_execute_shell_passes_preexec_fn_when_limits_requested(monkeypatch):
    captured = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    execute_shell("echo hi", cpu_seconds=5, memory_mb=256)

    assert captured["preexec_fn"] is not None


def test_execute_shell_omits_preexec_fn_when_no_limits_requested(monkeypatch):
    captured = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    execute_shell("echo hi")

    assert captured["preexec_fn"] is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits only")
def test_execute_shell_uses_settings_default_resource_limits(monkeypatch):
    monkeypatch.setattr(Settings, "SHELL_DEFAULT_CPU_SECONDS", 5)
    monkeypatch.setattr(Settings, "SHELL_DEFAULT_MEMORY_MB", 256)

    captured = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    execute_shell("echo hi")

    assert captured["preexec_fn"] is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits only")
def test_execute_shell_memory_limit_is_actually_enforced():
    # 1 MB of address space is far too little for even /bin/sh to
    # start — proves the limit is really applied, not just wired.
    with pytest.raises(subprocess.CalledProcessError):
        execute_shell("echo hi", memory_mb=1)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits only")
def test_execute_shell_cpu_limit_is_actually_enforced():
    with pytest.raises(subprocess.CalledProcessError):
        execute_shell(
            "python3 -c 'while True: pass'",
            cpu_seconds=1,
            timeout=15,
        )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits only")
def test_run_python_passes_preexec_fn_when_limits_requested(tmp_path, monkeypatch):
    script = tmp_path / "ok.py"
    script.write_text("print('hi')\n")

    captured = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    run_python(str(script), cpu_seconds=5)

    assert captured["preexec_fn"] is not None


# ---------------------------------------------------------------------
# execute_shell: approval staging
# ---------------------------------------------------------------------


def test_execute_shell_stages_instead_of_running_when_approver_active(tmp_path):
    ran = []

    def runner(request):
        ran.append(request.command)
        return subprocess.CompletedProcess(request.command, 0)

    manager = CommandApprovalManager(runner=runner)
    set_active_command_approver(manager)

    result = execute_shell("echo hi")

    assert isinstance(result, str)
    assert "staged for approval" in result
    assert ran == []
    assert manager.has_pending()
    assert manager.affected_commands() == ["echo hi"]


def test_execute_shell_still_checks_allowlist_and_denylist_while_staging():
    manager = CommandApprovalManager(runner=lambda r: None)
    set_active_command_approver(manager)

    with pytest.raises(UnsafeCommandError):
        execute_shell("rm -rf /")

    with pytest.raises(UnsafeCommandError, match="not in the allowed command list"):
        execute_shell("nc -lvp 4444")

    assert not manager.has_pending()


def test_approving_a_staged_command_actually_runs_it():
    from src.tools.shell_tools import _run_shell_command

    manager = CommandApprovalManager(runner=_run_shell_command)
    set_active_command_approver(manager)

    result = execute_shell("echo staged-and-approved")
    assert isinstance(result, str)

    results = manager.approve_all()

    assert len(results) == 1
    assert results[0].stdout.strip() == "staged-and-approved"
    assert not manager.has_pending()


def test_rejecting_a_staged_command_never_runs_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "ran.txt"

    def runner(request):
        marker.write_text("ran")
        return subprocess.CompletedProcess(request.command, 0)

    manager = CommandApprovalManager(runner=runner)
    set_active_command_approver(manager)

    execute_shell(f"touch {marker.name}")
    manager.discard_all()

    assert not marker.exists()


def _fail_if_called(request):
    raise AssertionError("should not need approval")


def test_operating_system_bypasses_approval_staging():
    manager = CommandApprovalManager(runner=_fail_if_called)
    set_active_command_approver(manager)

    assert isinstance(operating_system(), str)
    assert not manager.has_pending()


def test_current_user_bypasses_approval_staging():
    manager = CommandApprovalManager(runner=_fail_if_called)
    set_active_command_approver(manager)

    assert isinstance(current_user(), str)
    assert not manager.has_pending()


def test_execute_shell_without_active_approver_runs_directly():
    # Default (no approver set anywhere) — exactly pre-Phase-26
    # behavior, still returns a real CompletedProcess.
    result = execute_shell("echo hi")

    assert isinstance(result, subprocess.CompletedProcess)
    assert result.stdout.strip() == "hi"


# ---------------------------------------------------------------------
# _parse_pytest_summary (unit tests, no subprocess needed)
# ---------------------------------------------------------------------


class TestParsePytestSummary:
    def test_parses_all_passing(self):
        output = "...\n5 passed in 0.12s"
        assert _parse_pytest_summary(output) == (5, 0, 0)

    def test_parses_mixed_results(self):
        output = "..F.\n3 passed, 1 failed in 0.50s"
        assert _parse_pytest_summary(output) == (3, 1, 0)

    def test_parses_with_errors(self):
        output = "EE\n0 passed, 2 error in 0.10s"
        assert _parse_pytest_summary(output) == (0, 0, 2)

    def test_parses_all_three_counts(self):
        output = "..FE\n2 passed, 1 failed, 1 error in 1.00s"
        assert _parse_pytest_summary(output) == (2, 1, 1)

    def test_returns_zeros_for_no_summary_line(self):
        assert _parse_pytest_summary("no tests found") == (0, 0, 0)

    def test_uses_last_summary_line(self):
        # Extra context lines shouldn't confuse the parser
        output = "some output\n1 passed in 0.01s\n\n"
        assert _parse_pytest_summary(output) == (1, 0, 0)


# ---------------------------------------------------------------------
# run_tests (integration — actually invokes pytest on tmp test files)
# ---------------------------------------------------------------------


class TestRunTests:
    def test_returns_structured_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "test_pass.py").write_text("def test_ok(): assert True\n")

        result = run_tests(str(tmp_path))

        assert isinstance(result, dict)
        assert {"passed", "failed", "errors", "total", "exit_code", "output"} <= result.keys()

    def test_all_passing_gives_exit_0(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "test_pass.py").write_text("def test_ok(): assert True\n")

        result = run_tests(str(tmp_path))

        assert result["exit_code"] == 0
        assert result["passed"] >= 1
        assert result["failed"] == 0

    def test_failing_test_gives_exit_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "test_fail.py").write_text("def test_bad(): assert False\n")

        result = run_tests(str(tmp_path))

        assert result["exit_code"] == 1
        assert result["failed"] >= 1

    def test_total_equals_sum(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "test_mixed.py").write_text(
            "def test_ok(): assert True\ndef test_bad(): assert False\n"
        )

        result = run_tests(str(tmp_path))

        assert result["total"] == result["passed"] + result["failed"] + result["errors"]

    def test_output_is_string(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "test_pass.py").write_text("def test_ok(): assert True\n")

        result = run_tests(str(tmp_path))

        assert isinstance(result["output"], str)

    def test_bypasses_approval_staging(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "test_pass.py").write_text("def test_ok(): assert True\n")
        manager = CommandApprovalManager(runner=_fail_if_called)
        set_active_command_approver(manager)

        result = run_tests(str(tmp_path))

        # run_tests must bypass the approval gate — it's a fixed, safe tool
        assert not manager.has_pending()
        assert result["exit_code"] == 0


# ---------------------------------------------------------------------
# lint_file (Task 29)
# ---------------------------------------------------------------------

# Fake ruff JSON output with one issue
_RUFF_ONE_ISSUE = (
    '[{"code":"E501","message":"Line too long","filename":"a.py",'
    '"location":{"row":3,"column":1},"end_location":{"row":3,"column":95},'
    '"url":"https://example.com","fix":null,"noqa_row":3,"cell":null}]'
)
_RUFF_CLEAN = "[]"


def _make_ruff_proc(stdout: str, returncode: int) -> "subprocess.CompletedProcess":
    p = subprocess.CompletedProcess(args=[], returncode=returncode)
    p.stdout = stdout
    p.stderr = ""
    return p


class TestLintFile:
    def test_raises_if_ruff_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        with pytest.raises(RuntimeError, match="ruff is not installed"):
            lint_file(str(f))

    def test_rejects_path_outside_workspace(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        outside = tmp_path.parent / "escape.py"
        outside.write_text("x = 1")
        try:
            with pytest.raises(PermissionError):
                lint_file(str(outside))
        finally:
            outside.unlink(missing_ok=True)

    def test_returns_structured_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/ruff")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_ruff_proc(_RUFF_CLEAN, 0),
        )

        result = lint_file(str(f))

        assert isinstance(result, dict)
        assert {"issues", "total", "exit_code"} <= result.keys()

    def test_parses_issues_from_json_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/ruff")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_ruff_proc(_RUFF_ONE_ISSUE, 1),
        )

        result = lint_file(str(f))

        assert result["total"] == 1
        assert result["exit_code"] == 1
        issue = result["issues"][0]
        assert issue["code"] == "E501"
        assert issue["line"] == 3
        assert issue["column"] == 1
        assert "Line too long" in issue["message"]

    def test_clean_file_returns_no_issues(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/ruff")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_ruff_proc(_RUFF_CLEAN, 0),
        )

        result = lint_file(str(f))

        assert result["issues"] == []
        assert result["total"] == 0
        assert result["exit_code"] == 0

    def test_bypasses_approval_staging(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/ruff")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _make_ruff_proc(_RUFF_CLEAN, 0),
        )
        manager = CommandApprovalManager(runner=_fail_if_called)
        set_active_command_approver(manager)

        result = lint_file(str(f))

        assert not manager.has_pending()
        assert result["exit_code"] == 0
