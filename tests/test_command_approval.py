import subprocess

import pytest

from src.tools.command_approval import CommandApprovalManager, ShellCommandRequest


def _request(command: str = "echo hi") -> ShellCommandRequest:
    return ShellCommandRequest(command=command, cwd=".", timeout=30)


def test_propose_stages_without_running():
    ran = []

    def runner(request):
        ran.append(request)
        return subprocess.CompletedProcess(request.command, 0)

    manager = CommandApprovalManager(runner=runner)
    manager.propose(_request("echo hi"))

    assert ran == []
    assert manager.has_pending()
    assert manager.affected_commands() == ["echo hi"]


def test_pending_returns_a_copy():
    manager = CommandApprovalManager(runner=lambda r: None)
    manager.propose(_request())

    pending = manager.pending
    pending.clear()

    assert manager.has_pending()


def test_approve_all_runs_each_command_in_proposal_order():
    ran = []

    def runner(request):
        ran.append(request.command)
        return subprocess.CompletedProcess(request.command, 0)

    manager = CommandApprovalManager(runner=runner)
    manager.propose(_request("echo one"))
    manager.propose(_request("echo two"))

    results = manager.approve_all()

    assert ran == ["echo one", "echo two"]
    assert [r.args for r in results] == ["echo one", "echo two"]
    assert not manager.has_pending()


def test_approve_all_on_empty_manager_returns_empty_list():
    manager = CommandApprovalManager(runner=lambda r: None)

    assert manager.approve_all() == []


def test_approve_all_propagates_a_failing_command_and_stops():
    ran = []

    def runner(request):
        ran.append(request.command)
        if request.command == "boom":
            raise subprocess.CalledProcessError(1, request.command)
        return subprocess.CompletedProcess(request.command, 0)

    manager = CommandApprovalManager(runner=runner)
    manager.propose(_request("ok"))
    manager.propose(_request("boom"))
    manager.propose(_request("never runs"))

    with pytest.raises(subprocess.CalledProcessError):
        manager.approve_all()

    # Fail-stop: the third command was never reached.
    assert ran == ["ok", "boom"]


def test_discard_all_runs_nothing():
    ran = []

    def runner(request):
        ran.append(request)
        return subprocess.CompletedProcess(request.command, 0)

    manager = CommandApprovalManager(runner=runner)
    manager.propose(_request("echo one"))
    manager.propose(_request("echo two"))

    discarded = manager.discard_all()

    assert discarded == ["echo one", "echo two"]
    assert ran == []
    assert not manager.has_pending()


def test_affected_commands_reflects_proposal_order():
    manager = CommandApprovalManager(runner=lambda r: None)
    manager.propose(_request("first"))
    manager.propose(_request("second"))
    manager.propose(_request("third"))

    assert manager.affected_commands() == ["first", "second", "third"]
