from src.tools.shell_tools import (
    current_user,
    execute_shell,
    is_command_available,
    ls,
    operating_system,
    pwd,
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


def test_which():
    assert which("python3") is not None


def test_command_exists():
    assert is_command_available("python3")


def test_current_user():
    assert isinstance(current_user(), str)


def test_operating_system():
    assert isinstance(operating_system(), str)