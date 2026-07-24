import pytest

from src.memory.workspace_memory import WorkspaceMemory
from src.tools.context_manager import (
    ContextBuilder,
    ContextConfig,
    ContextManager,
    estimate_tokens,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(tmp_path, relpath, content):
    file_path = tmp_path / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


# ---------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------


def test_estimate_tokens_empty_string_is_zero():
    assert estimate_tokens("") == 0


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("abc") == 1  # rounds up to at least 1


# ---------------------------------------------------------------------
# File ranking: small repository
# ---------------------------------------------------------------------


def test_rank_files_matches_symbol_name(_workspace):
    _write(
        _workspace,
        "auth.py",
        "def login(user):\n    return True\n",
    )
    _write(_workspace, "unrelated.py", "def noop():\n    pass\n")

    manager = ContextManager()
    ranked = manager.rank_files("please fix the login function")

    assert ranked[0].path == "auth.py"
    assert any("symbol match" in reason for reason in ranked[0].reasons)


def test_rank_files_matches_filename(_workspace):
    _write(_workspace, "payments.py", "x = 1\n")
    _write(_workspace, "other.py", "y = 2\n")

    manager = ContextManager()
    ranked = manager.rank_files("investigate the payments module")

    paths = [r.path for r in ranked]
    assert "payments.py" in paths
    assert ranked[0].path == "payments.py"


def test_rank_files_no_match_returns_empty():
    manager = ContextManager()
    ranked = manager.rank_files("totally unrelated gibberish query zzz")

    assert ranked == []


# ---------------------------------------------------------------------
# File ranking: duplicate removal
# ---------------------------------------------------------------------


def test_rank_files_deduplicates_files_matched_by_multiple_signals(
    _workspace,
):
    # "login" matches both the filename and a symbol inside it —
    # the file must still only appear once, with a combined score.
    _write(
        _workspace,
        "login.py",
        "def login(user):\n    return True\n",
    )

    manager = ContextManager()
    ranked = manager.rank_files("login")

    matching = [r for r in ranked if r.path == "login.py"]
    assert len(matching) == 1
    assert len(matching[0].reasons) >= 2


# ---------------------------------------------------------------------
# File ranking: max_files budgeting
# ---------------------------------------------------------------------


def test_rank_files_respects_max_files(_workspace):
    for i in range(10):
        _write(_workspace, f"module_{i}.py", f"def handler_{i}(): pass\n")

    manager = ContextManager(ContextConfig(max_files=3))
    ranked = manager.rank_files("handler")

    assert len(ranked) == 3


# ---------------------------------------------------------------------
# Ignored directories
# ---------------------------------------------------------------------


def test_rank_files_ignores_vendor_and_cache_directories(_workspace):
    _write(_workspace, "node_modules/pkg/index.py", "def login(): pass\n")
    _write(_workspace, ".venv/lib/site.py", "def login(): pass\n")
    _write(_workspace, "dist/built.py", "def login(): pass\n")
    _write(_workspace, "build/out.py", "def login(): pass\n")
    _write(_workspace, "__pycache__/cached.py", "def login(): pass\n")
    _write(_workspace, "real_code/auth.py", "def login(): pass\n")

    manager = ContextManager()
    ranked = manager.rank_files("login")

    paths = [r.path for r in ranked]
    assert paths == ["real_code/auth.py"]


# ---------------------------------------------------------------------
# Workspace-assisted ranking
# ---------------------------------------------------------------------


def test_rank_files_boosted_by_recently_edited_files(_workspace):
    _write(_workspace, "a.py", "x = 1\n")
    _write(_workspace, "b.py", "y = 2\n")

    memory = WorkspaceMemory()
    memory.record_file_modified(str(_workspace / "a.py"))

    manager = ContextManager()
    # Query matches neither file directly — only the workspace signal
    # should surface anything.
    ranked = manager.rank_files(
        "what should I look at", workspace_memory=memory
    )

    assert len(ranked) == 1
    assert ranked[0].path == "a.py"
    assert any("recently edited" in r for r in ranked[0].reasons)


def test_rank_files_boosted_by_recently_created_symbols(_workspace):
    _write(_workspace, "a.py", "def brand_new_helper():\n    pass\n")

    memory = WorkspaceMemory()
    memory.record_symbol_added("brand_new_helper", kind="function", file="a.py")

    manager = ContextManager()
    ranked = manager.rank_files("continue working", workspace_memory=memory)

    assert ranked[0].path == "a.py"
    assert any("recently created symbol" in r for r in ranked[0].reasons)


def test_rank_files_without_workspace_memory_ignores_session_signals(
    _workspace,
):
    _write(_workspace, "a.py", "x = 1\n")

    manager = ContextManager()
    ranked = manager.rank_files("nothing relevant here")

    assert ranked == []


# ---------------------------------------------------------------------
# ContextBuilder: small repositories / files
# ---------------------------------------------------------------------


def test_context_builder_includes_matched_file_verbatim(_workspace):
    _write(
        _workspace,
        "auth.py",
        "import os\n\n\ndef login(user):\n    return True\n",
    )

    builder = ContextBuilder()
    context = builder.build("fix the login function")

    assert "## auth.py" in context
    assert "def login(user):" in context
    assert "Imports: os" in context
    assert "Public API: login" in context


def test_context_builder_returns_empty_when_nothing_relevant():
    builder = ContextBuilder()

    assert builder.build("completely unrelated request xyz") == ""


def test_context_builder_no_duplicate_sections(_workspace):
    _write(_workspace, "login.py", "def login(user):\n    return True\n")

    builder = ContextBuilder()
    context = builder.build("login")

    assert context.count("## login.py") == 1


# ---------------------------------------------------------------------
# ContextBuilder: large repositories / compression
# ---------------------------------------------------------------------


def _big_file_source(target_lines: int) -> str:
    filler = "\n".join(f"x_{i} = {i}" for i in range(target_lines))
    return (
        "import os\n\n"
        "def unrelated_helper():\n"
        "    pass\n\n"
        f"{filler}\n\n"
        "# TODO: clean this module up\n\n"
        "def target_function(payload):\n"
        "    return payload\n"
    )


def test_context_builder_compresses_large_files(_workspace):
    source = _big_file_source(300)
    _write(_workspace, "huge.py", source)

    builder = ContextBuilder(ContextConfig(compression_threshold=50))
    context = builder.build("target_function")

    assert "## huge.py" in context
    assert "compressed:" in context
    assert "def target_function(payload):" in context
    # The unrelated filler lines should not all be dumped verbatim.
    assert "x_150 = 150" not in context


def test_context_builder_keeps_small_files_uncompressed(_workspace):
    _write(_workspace, "small.py", "def target_function():\n    pass\n")

    builder = ContextBuilder(ContextConfig(compression_threshold=50))
    context = builder.build("target_function")

    assert "compressed:" not in context
    assert "```\n" in context


def test_context_builder_preserves_todo_comments_when_compressing(
    _workspace,
):
    source = _big_file_source(300)
    _write(_workspace, "huge.py", source)

    builder = ContextBuilder(ContextConfig(compression_threshold=50))
    context = builder.build("target_function")

    assert "TODO: clean this module up" in context


# ---------------------------------------------------------------------
# ContextBuilder: token budgeting
# ---------------------------------------------------------------------


def test_context_builder_respects_token_budget(_workspace):
    for i in range(5):
        _write(
            _workspace,
            f"handler_{i}.py",
            f"def handler_{i}():\n    return {i}\n",
        )

    generous = ContextBuilder(ContextConfig(max_context_tokens=100000))
    tight = ContextBuilder(ContextConfig(max_context_tokens=1))

    full_context = generous.build("handler")
    tiny_context = tight.build("handler")

    assert full_context.count("## handler_") == 5
    assert tiny_context.count("## handler_") <= 1


def test_context_builder_stops_once_budget_exhausted(_workspace):
    for i in range(20):
        _write(
            _workspace,
            f"handler_{i}.py",
            f"def handler_{i}():\n    return {i}\n",
        )

    # Small enough to fit only a handful of the 20 matching files.
    config = ContextConfig(max_context_tokens=200, max_files=20)
    builder = ContextBuilder(config)

    context = builder.build("handler")
    included = context.count("## handler_")

    assert 0 < included < 20
    assert estimate_tokens(context) <= config.max_context_tokens + 50


# ---------------------------------------------------------------------
# ContextBuilder: workspace summary inclusion
# ---------------------------------------------------------------------


def test_context_builder_includes_workspace_summary(_workspace):
    _write(_workspace, "a.py", "def login():\n    pass\n")

    memory = WorkspaceMemory()
    memory.record_file_modified("a.py")
    memory.remember("todo", "wire up the login flow")

    builder = ContextBuilder()
    context = builder.build("login", workspace_memory=memory)

    assert "## Workspace summary" in context
    assert "Recently edited files: a.py" in context
    assert "Outstanding TODOs: wire up the login flow" in context


def test_context_builder_can_omit_workspace_summary(_workspace):
    _write(_workspace, "a.py", "def login():\n    pass\n")

    memory = WorkspaceMemory()
    memory.record_file_modified("a.py")

    builder = ContextBuilder()
    context = builder.build(
        "login", workspace_memory=memory, include_workspace_summary=False
    )

    assert "## Workspace summary" not in context


def test_context_builder_without_workspace_memory_has_no_summary(
    _workspace,
):
    _write(_workspace, "a.py", "def login():\n    pass\n")

    builder = ContextBuilder()
    context = builder.build("login")

    assert "## Workspace summary" not in context


# ---------------------------------------------------------------------
# Planner integration: ContextBuilder output is just a plain string
# ---------------------------------------------------------------------


def test_context_builder_output_feeds_directly_into_planner(
    _workspace, monkeypatch
):
    from pathlib import Path as _Path

    from src.agent.dispatcher import ToolDispatcher
    from src.agent.planner import Planner
    from src.tools.metadata import tool
    from src.tools.registry import ToolRegistry

    # Planner loads its prompt template by a path relative to the
    # repo root, independent of the workspace this test operates
    # in — resolve it to an absolute path before the autouse
    # `_workspace` fixture's chdir takes effect for anything else
    # in this test.
    repo_root = _Path(__file__).resolve().parents[1]

    _write(_workspace, "auth.py", "def login(user):\n    return True\n")

    @tool(description="No-op.")
    def noop() -> None:
        return None

    registry = ToolRegistry()
    registry.register(noop)
    planner = Planner(registry, ToolDispatcher(registry))
    monkeypatch.setattr(
        planner, "PROMPT_FILE", str(repo_root / "src/prompts/planning.txt")
    )

    captured = []

    def _fake_generate_json(prompt):
        captured.append(prompt)
        return {"steps": [{"tool": "none", "arguments": {}}]}

    monkeypatch.setattr(planner.client, "generate_json", _fake_generate_json)

    context = ContextBuilder().build("fix the login function")
    planner.plan("fix the login bug", workspace_context=context)

    assert "## auth.py" in captured[0]
    assert "def login(user):" in captured[0]
    assert "Current workspace context" in captured[0]
