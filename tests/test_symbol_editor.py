import pytest

from src.tools.edit_tools import set_active_patch_manager
from src.tools.patch_manager import ChangeManager
from src.tools.symbol_editor import (
    SymbolEditor,
    SymbolNotFoundError,
    UnsupportedLanguageError,
    find_class,
    find_function,
    find_method,
    insert_after_symbol,
    insert_before_symbol,
    replace_class,
    replace_function,
    replace_method,
)

SAMPLE = '''"""Sample module docstring."""

import os
from pathlib import Path


def foo(a, b):
    """Add two numbers."""
    return a + b


# A helpful comment right before bar.
@staticmethod
def bar():
    return "bar"


class Greeter:
    """Greets people."""

    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello, {self.name}"


class Empty:
    pass
'''


@pytest.fixture(autouse=True)
def _no_leaked_patch_manager():
    yield
    set_active_patch_manager(None)


# ---------------------------------------------------------------------
# SymbolEditor: finding
# ---------------------------------------------------------------------


def test_find_function_locates_correct_span():
    editor = SymbolEditor("mod.py", SAMPLE)

    location = editor.find_function("foo")

    assert location.name == "foo"
    assert location.kind == "function"
    assert location.source.startswith("def foo(a, b):")
    assert location.source.strip().endswith("return a + b")


def test_find_function_includes_decorators_in_span():
    editor = SymbolEditor("mod.py", SAMPLE)

    location = editor.find_function("bar")

    # The decorator line, not the `def` line, is the start.
    assert location.source.startswith("@staticmethod")
    assert "def bar():" in location.source
    # The comment above the decorator is NOT part of the symbol span.
    assert "# A helpful comment" not in location.source


def test_find_class_locates_correct_span():
    editor = SymbolEditor("mod.py", SAMPLE)

    location = editor.find_class("Greeter")

    assert location.name == "Greeter"
    assert location.kind == "class"
    assert location.source.startswith("class Greeter:")
    assert "def greet(self):" in location.source
    assert "class Empty" not in location.source


def test_find_method_locates_nested_method():
    editor = SymbolEditor("mod.py", SAMPLE)

    location = editor.find_method("Greeter", "greet")

    assert location.name == "greet"
    assert location.kind == "method"
    assert location.source.strip().startswith("def greet(self):")
    assert "__init__" not in location.source


def test_find_method_locates_init():
    editor = SymbolEditor("mod.py", SAMPLE)

    location = editor.find_method("Greeter", "__init__")

    assert "self.name = name" in location.source


def test_find_function_missing_raises():
    editor = SymbolEditor("mod.py", SAMPLE)

    with pytest.raises(SymbolNotFoundError):
        editor.find_function("does_not_exist")


def test_find_class_missing_raises():
    editor = SymbolEditor("mod.py", SAMPLE)

    with pytest.raises(SymbolNotFoundError):
        editor.find_class("DoesNotExist")


def test_find_method_missing_class_raises():
    editor = SymbolEditor("mod.py", SAMPLE)

    with pytest.raises(SymbolNotFoundError):
        editor.find_method("DoesNotExist", "greet")


def test_find_method_missing_method_raises():
    editor = SymbolEditor("mod.py", SAMPLE)

    with pytest.raises(SymbolNotFoundError):
        editor.find_method("Greeter", "does_not_exist")


# ---------------------------------------------------------------------
# SymbolEditor: replace function
# ---------------------------------------------------------------------


def test_replace_function_preserves_rest_of_file():
    editor = SymbolEditor("mod.py", SAMPLE)

    updated = editor.replace_function("foo", "def foo(a, b):\n    return a - b\n")

    assert "def foo(a, b):\n    return a - b\n" in updated
    assert "return a + b" not in updated
    # Everything else is untouched, including the docstring, imports,
    # the comment before bar, and the decorator.
    assert '"""Sample module docstring."""' in updated
    assert "import os\nfrom pathlib import Path" in updated
    assert "# A helpful comment right before bar." in updated
    assert "@staticmethod\ndef bar():" in updated
    assert "class Greeter:" in updated
    assert "class Empty:" in updated


def test_replace_function_removes_old_decorators_too():
    editor = SymbolEditor("mod.py", SAMPLE)

    updated = editor.replace_function("bar", "def bar():\n    return 'baz'\n")

    assert "@staticmethod" not in updated
    assert "def bar():\n    return 'baz'\n" in updated


# ---------------------------------------------------------------------
# SymbolEditor: replace class
# ---------------------------------------------------------------------


def test_replace_class_preserves_rest_of_file():
    editor = SymbolEditor("mod.py", SAMPLE)

    new_class = (
        "class Greeter:\n"
        '    """Greets people, loudly."""\n\n'
        "    def __init__(self, name):\n"
        "        self.name = name\n\n"
        "    def greet(self):\n"
        '        return f"HELLO, {self.name}!"\n'
    )

    updated = editor.replace_class("Greeter", new_class)

    assert "HELLO" in updated
    assert "Hello," not in updated
    assert "def foo(a, b):" in updated
    assert "class Empty:" in updated


def test_replace_method_preserves_class_and_siblings():
    editor = SymbolEditor("mod.py", SAMPLE)

    updated = editor.replace_method(
        "Greeter",
        "greet",
        "    def greet(self):\n        return 'hi'\n",
    )

    assert "return 'hi'" in updated
    assert "def __init__(self, name):" in updated
    assert "self.name = name" in updated
    assert "class Empty:" in updated


# ---------------------------------------------------------------------
# SymbolEditor: insert before/after
# ---------------------------------------------------------------------


def test_insert_after_symbol_places_code_right_after_the_symbol():
    editor = SymbolEditor("mod.py", SAMPLE)

    updated = editor.insert_after_symbol("foo", "def inserted():\n    return 42\n")

    foo_index = updated.index("def foo(a, b):")
    inserted_index = updated.index("def inserted():")
    bar_index = updated.index("def bar():")

    assert foo_index < inserted_index < bar_index


def test_insert_before_symbol_places_code_right_before_the_symbol():
    editor = SymbolEditor("mod.py", SAMPLE)

    updated = editor.insert_before_symbol("Greeter", "def inserted():\n    return 42\n")

    bar_index = updated.index("def bar():")
    inserted_index = updated.index("def inserted():")
    class_index = updated.index("class Greeter:")

    assert bar_index < inserted_index < class_index


# ---------------------------------------------------------------------
# SymbolEditor: syntax errors
# ---------------------------------------------------------------------


def test_symbol_editor_rejects_unparsable_python():
    with pytest.raises(UnsupportedLanguageError):
        SymbolEditor("broken.py", "def broken(:\n    pass\n")


# ---------------------------------------------------------------------
# @tool functions: apply mode
# ---------------------------------------------------------------------


@pytest.fixture
def sample_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    file = tmp_path / "mod.py"
    file.write_text(SAMPLE)
    return file


def test_tool_find_function_returns_dict(sample_file):
    result = find_function("foo", path=str(sample_file))

    assert result["name"] == "foo"
    assert result["kind"] == "function"
    assert result["file"] == str(sample_file)
    assert "def foo" in result["source"]


def test_tool_find_class_returns_dict(sample_file):
    result = find_class("Greeter", path=str(sample_file))

    assert result["kind"] == "class"


def test_tool_find_method_returns_dict_for_nested_method(sample_file):
    result = find_method("Greeter", "greet", path=str(sample_file))

    assert result["kind"] == "method"
    assert result["name"] == "greet"


def test_tool_replace_function_writes_file_in_apply_mode(sample_file):
    replace_function("foo", "def foo(a, b):\n    return a * b\n", path=str(sample_file))

    content = sample_file.read_text()
    assert "return a * b" in content
    assert "return a + b" not in content
    assert "class Greeter:" in content


def test_tool_replace_class_writes_file_in_apply_mode(sample_file):
    replace_class(
        "Empty",
        "class Empty:\n    value = 1\n",
        path=str(sample_file),
    )

    content = sample_file.read_text()
    assert "value = 1" in content
    assert "def foo(a, b):" in content


def test_tool_insert_after_symbol_writes_file_in_apply_mode(sample_file):
    insert_after_symbol("foo", "def new_func():\n    return 1\n", path=str(sample_file))

    content = sample_file.read_text()
    assert content.index("def foo") < content.index("def new_func")
    assert content.index("def new_func") < content.index("def bar")


def test_tool_insert_before_symbol_writes_file_in_apply_mode(sample_file):
    insert_before_symbol(
        "foo", "def new_func():\n    return 1\n", path=str(sample_file)
    )

    content = sample_file.read_text()
    assert content.index("def new_func") < content.index("def foo(a, b):")


# ---------------------------------------------------------------------
# @tool functions: preview mode (never writes)
# ---------------------------------------------------------------------


def test_tool_replace_function_preview_mode_stages_instead_of_writing(
    sample_file,
):
    manager = ChangeManager()
    set_active_patch_manager(manager)

    original_content = sample_file.read_text()

    result = replace_function(
        "foo", "def foo(a, b):\n    return a * b\n", path=str(sample_file)
    )

    assert sample_file.read_text() == original_content  # untouched
    assert manager.has_pending()
    edit = manager.pending[0]
    assert edit.path == str(sample_file)
    assert edit.original_content == original_content
    assert "return a * b" in edit.updated_content
    assert "return a + b" not in edit.updated_content
    assert "-    return a + b" in edit.diff
    assert "+    return a * b" in edit.diff
    assert isinstance(result, str)
    assert "Preview" in result


def test_tool_replace_class_preview_mode_stages_instead_of_writing(
    sample_file,
):
    manager = ChangeManager()
    set_active_patch_manager(manager)

    replace_class("Empty", "class Empty:\n    value = 1\n", path=str(sample_file))

    assert manager.has_pending()
    assert "value = 1" in manager.pending[0].updated_content
    assert "class Empty:\n    pass" in sample_file.read_text()  # untouched


def test_tool_insert_after_symbol_preview_mode_stages_instead_of_writing(
    sample_file,
):
    manager = ChangeManager()
    set_active_patch_manager(manager)

    insert_after_symbol("foo", "def new_func():\n    return 1\n", path=str(sample_file))

    assert manager.has_pending()
    assert "def new_func" not in sample_file.read_text()
    assert "def new_func" in manager.pending[0].updated_content


# ---------------------------------------------------------------------
# Fallback behavior for unsupported languages
# ---------------------------------------------------------------------


def test_replace_function_falls_back_with_clear_error_for_non_python_file(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    text_file = tmp_path / "notes.txt"
    text_file.write_text("def foo(): pass\n")

    with pytest.raises(UnsupportedLanguageError, match="replace_in_file"):
        replace_function("foo", "def foo(): pass\n", path=str(text_file))

    assert text_file.read_text() == "def foo(): pass\n"  # untouched


def test_find_function_falls_back_with_clear_error_for_non_python_file(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    text_file = tmp_path / "notes.md"
    text_file.write_text("# foo\n")

    with pytest.raises(UnsupportedLanguageError):
        find_function("foo", path=str(text_file))


def test_replace_function_on_syntactically_invalid_python_raises_clearly(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n    pass\n")

    with pytest.raises(UnsupportedLanguageError):
        replace_function("broken", "def broken():\n    pass\n", path=str(broken))


def test_tool_missing_file_raises_file_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError):
        find_function("foo", path=str(tmp_path / "missing.py"))


# ---------------------------------------------------------------------
# Reuse of the existing RepositoryIndex (path resolution without `path`)
# ---------------------------------------------------------------------


def test_find_function_resolves_file_via_repository_index_when_no_path(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text(SAMPLE)

    result = find_function("foo")

    assert result["file"] == str((pkg / "a.py").resolve())
    assert result["kind"] == "function"


def test_replace_function_resolves_file_via_repository_index_when_no_path(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text(SAMPLE)

    replace_function("foo", "def foo(a, b):\n    return a * b\n")

    assert "return a * b" in (tmp_path / "a.py").read_text()


def test_find_function_without_path_raises_when_symbol_unknown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text(SAMPLE)

    with pytest.raises(SymbolNotFoundError):
        find_function("totally_unknown_symbol")


def test_replace_function_refreshes_repository_index(tmp_path, monkeypatch):
    from src.tools.repo_tools import find_symbol

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text(SAMPLE)

    find_symbol("foo")

    replace_function("foo", "def foo(a, b, c):\n    return a + b + c\n")

    locations = find_symbol("foo")
    assert len(locations) == 1
    assert locations[0]["file"] == "a.py"
    assert locations[0]["type"] == "function"


# ---------------------------------------------------------------------------
# replace_method tool (M4)
# ---------------------------------------------------------------------------


def test_tool_replace_method_writes_file_in_apply_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = "class Greeter:\n    def greet(self):\n        return 'hello'\n"
    (tmp_path / "a.py").write_text(src)

    result = replace_method(
        "Greeter",
        "greet",
        "    def greet(self):\n        return 'hi'\n",
        path=str(tmp_path / "a.py"),
    )

    assert "replaced method" in result
    text = (tmp_path / "a.py").read_text()
    assert "return 'hi'" in text
    assert "return 'hello'" not in text


def test_tool_replace_method_preview_mode_stages_instead_of_writing(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    src = "class Greeter:\n    def greet(self):\n        return 'hello'\n"
    (tmp_path / "a.py").write_text(src)

    pm = ChangeManager()
    set_active_patch_manager(pm)
    try:
        replace_method(
            "Greeter",
            "greet",
            "    def greet(self):\n        return 'hi'\n",
            path=str(tmp_path / "a.py"),
        )
        assert len(pm.pending) == 1
        assert (tmp_path / "a.py").read_text() == src
    finally:
        set_active_patch_manager(None)


def test_tool_replace_method_raises_when_class_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("class Foo: pass\n")

    with pytest.raises(SymbolNotFoundError):
        replace_method("NonExistent", "some_method", "    pass\n", path=str(tmp_path / "a.py"))
