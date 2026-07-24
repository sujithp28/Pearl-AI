import pytest

from src.memory import ConversationTurn, ExecutionRecord, Memory, Task

# ---------------------------------------------------------------------
# Conversation memory
# ---------------------------------------------------------------------


def test_record_turn_appends_conversation():
    memory = Memory()

    memory.record_turn("user", "hello")
    memory.record_turn("agent", "hi there")

    assert [t.role for t in memory.conversation] == ["user", "agent"]
    assert [t.content for t in memory.conversation] == ["hello", "hi there"]
    assert all(isinstance(t, ConversationTurn) for t in memory.conversation)


def test_recent_conversation_respects_limit():
    memory = Memory()

    for index in range(5):
        memory.record_turn("user", f"message {index}")

    recent = memory.recent_conversation(limit=2)

    assert [t.content for t in recent] == ["message 3", "message 4"]


def test_recent_conversation_without_limit_returns_all():
    memory = Memory()

    memory.record_turn("user", "a")
    memory.record_turn("agent", "b")

    assert len(memory.recent_conversation()) == 2


# ---------------------------------------------------------------------
# Task memory
# ---------------------------------------------------------------------


def test_start_task_creates_in_progress_task():
    memory = Memory()

    task = memory.start_task("refactor the parser")

    assert isinstance(task, Task)
    assert task.status == "in_progress"
    assert task.description == "refactor the parser"
    assert task in memory.tasks


def test_task_ids_increment():
    memory = Memory()

    first = memory.start_task("task one")
    second = memory.start_task("task two")

    assert first.id == "task-1"
    assert second.id == "task-2"


def test_complete_task_updates_status():
    memory = Memory()

    task = memory.start_task("do something")
    updated = memory.complete_task(task.id, status="completed")

    assert updated.status == "completed"
    assert memory.get_task(task.id).status == "completed"


def test_complete_task_default_status():
    memory = Memory()

    task = memory.start_task("do something")
    memory.complete_task(task.id)

    assert memory.get_task(task.id).status == "completed"


def test_get_task_unknown_id_raises_key_error():
    memory = Memory()

    with pytest.raises(KeyError):
        memory.get_task("task-999")


# ---------------------------------------------------------------------
# Project memory
# ---------------------------------------------------------------------


def test_remember_and_recall_project_fact():
    memory = Memory()

    memory.remember_project_fact("language", "python")

    assert memory.recall_project_fact("language") == "python"


def test_recall_unknown_fact_returns_default():
    memory = Memory()

    assert memory.recall_project_fact("missing") is None
    assert memory.recall_project_fact("missing", default="n/a") == "n/a"


# ---------------------------------------------------------------------
# Execution history
# ---------------------------------------------------------------------


def test_record_execution_success():
    memory = Memory()

    record = memory.record_execution("read_file", {"path": "a.txt"}, result="hi")

    assert isinstance(record, ExecutionRecord)
    assert record.succeeded
    assert record.result == "hi"
    assert record in memory.execution_history


def test_record_execution_failure():
    memory = Memory()

    record = memory.record_execution(
        "read_file", {"path": "missing.txt"}, error="not found"
    )

    assert not record.succeeded
    assert record.error == "not found"


def test_recent_executions_respects_limit():
    memory = Memory()

    for index in range(4):
        memory.record_execution("tool", {"n": index}, result=index)

    recent = memory.recent_executions(limit=2)

    assert [r.result for r in recent] == [2, 3]


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------


def test_to_dict_and_from_dict_round_trip():
    memory = Memory()

    memory.record_turn("user", "hello")
    task = memory.start_task("build feature")
    memory.complete_task(task.id, status="completed")
    memory.remember_project_fact("language", "python")
    memory.record_execution("read_file", {"path": "a.txt"}, result="hi")

    data = memory.to_dict()
    restored = Memory.from_dict(data)

    assert [t.content for t in restored.conversation] == ["hello"]
    assert restored.tasks[0].description == "build feature"
    assert restored.tasks[0].status == "completed"
    assert restored.project == {"language": "python"}
    assert restored.execution_history[0].tool_name == "read_file"
    assert restored.execution_history[0].result == "hi"


def test_from_dict_resumes_task_counter():
    memory = Memory()
    memory.start_task("first")
    memory.start_task("second")

    restored = Memory.from_dict(memory.to_dict())
    third = restored.start_task("third")

    assert third.id == "task-3"


def test_save_and_load_round_trip(tmp_path):
    memory = Memory()
    memory.record_turn("user", "hello")
    memory.start_task("build feature")
    memory.remember_project_fact("language", "python")
    memory.record_execution("read_file", {"path": "a.txt"}, result="hi")

    path = tmp_path / "memory.json"
    memory.save(path)

    loaded = Memory.load(path)

    assert [t.content for t in loaded.conversation] == ["hello"]
    assert loaded.tasks[0].description == "build feature"
    assert loaded.project == {"language": "python"}
    assert loaded.execution_history[0].result == "hi"


def test_save_creates_parent_directories(tmp_path):
    memory = Memory()
    path = tmp_path / "nested" / "dir" / "memory.json"

    memory.save(path)

    assert path.exists()


def test_load_missing_file_returns_empty_memory(tmp_path):
    memory = Memory.load(tmp_path / "does-not-exist.json")

    assert memory.conversation == []
    assert memory.tasks == []
    assert memory.project == {}
    assert memory.execution_history == []


def test_save_handles_non_json_serializable_result(tmp_path):
    memory = Memory()

    class Unserializable:
        def __str__(self) -> str:
            return "<unserializable>"

    memory.record_execution("weird_tool", {}, result=Unserializable())

    path = tmp_path / "memory.json"
    memory.save(path)

    loaded = Memory.load(path)

    assert loaded.execution_history[0].result == "<unserializable>"
