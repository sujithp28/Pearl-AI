"""
Pearl's memory system.

Tracks four kinds of structured, in-process memory:

- Conversation: the running user/agent dialogue.
- Tasks: higher-level goals requested by the user (e.g. via the
  planner) and their status.
- Project: durable key/value facts learned about the project.
- Execution history: a record of every tool invocation and its
  outcome.

No vector database or embeddings are used here; everything is
plain, JSON-serializable structured data that can be saved to and
loaded back from disk.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_PATH = Path(".pearl") / "memory.json"


def _timestamp() -> str:
    """
    Return the current UTC time as an ISO 8601 string.
    """

    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ConversationTurn:
    """
    A single turn in the conversation.
    """

    role: str
    content: str
    timestamp: str = field(default_factory=_timestamp)


@dataclass(slots=True)
class Task:
    """
    A higher-level goal the agent is (or was) working on.
    """

    id: str
    description: str
    status: str = "pending"
    created_at: str = field(default_factory=_timestamp)
    updated_at: str = field(default_factory=_timestamp)


@dataclass(slots=True)
class ExecutionRecord:
    """
    The outcome of a single tool execution.
    """

    tool_name: str
    kwargs: dict[str, Any]
    result: Any = None
    error: str | None = None
    timestamp: str = field(default_factory=_timestamp)

    @property
    def succeeded(self) -> bool:
        """
        Return whether the execution completed without error.
        """

        return self.error is None


class Memory:
    """
    Structured, in-process memory for Pearl.
    """

    def __init__(self) -> None:
        self.conversation: list[ConversationTurn] = []
        self.tasks: list[Task] = []
        self.project: dict[str, Any] = {}
        self.execution_history: list[ExecutionRecord] = []
        self._task_counter = 0

    # -- Conversation memory ------------------------------------------

    def record_turn(self, role: str, content: str) -> ConversationTurn:
        """
        Record one turn of the conversation.
        """

        turn = ConversationTurn(role=role, content=content)
        self.conversation.append(turn)

        logger.info("Recorded %s turn.", role)

        return turn

    def recent_conversation(
        self,
        limit: int | None = None,
    ) -> list[ConversationTurn]:
        """
        Return conversation turns, oldest first, optionally limited
        to the most recent `limit`.
        """

        if limit is None:
            return list(self.conversation)

        return self.conversation[-limit:]

    # -- Task memory ------------------------------------------------------

    def start_task(self, description: str) -> Task:
        """
        Start tracking a new task.
        """

        self._task_counter += 1

        task = Task(
            id=f"task-{self._task_counter}",
            description=description,
            status="in_progress",
        )
        self.tasks.append(task)

        logger.info("Started task %s: %s", task.id, description)

        return task

    def complete_task(self, task_id: str, status: str = "completed") -> Task:
        """
        Update a task's status.
        """

        task = self.get_task(task_id)
        task.status = status
        task.updated_at = _timestamp()

        logger.info("Task %s marked %s.", task_id, status)

        return task

    def get_task(self, task_id: str) -> Task:
        """
        Return a tracked task by id.
        """

        for task in self.tasks:
            if task.id == task_id:
                return task

        raise KeyError(f"Unknown task: {task_id}")

    # -- Project memory ---------------------------------------------------

    def remember_project_fact(self, key: str, value: Any) -> None:
        """
        Store a durable fact about the project.
        """

        self.project[key] = value

        logger.info("Remembered project fact: %s", key)

    def recall_project_fact(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a previously remembered project fact.
        """

        return self.project.get(key, default)

    # -- Execution history --------------------------------------------------

    def record_execution(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        result: Any = None,
        error: str | None = None,
    ) -> ExecutionRecord:
        """
        Record the outcome of a tool execution.
        """

        record = ExecutionRecord(
            tool_name=tool_name,
            kwargs=dict(kwargs),
            result=result,
            error=error,
        )
        self.execution_history.append(record)

        logger.info(
            "Recorded execution of '%s' (%s).",
            tool_name,
            "ok" if record.succeeded else "failed",
        )

        return record

    def recent_executions(
        self,
        limit: int | None = None,
    ) -> list[ExecutionRecord]:
        """
        Return execution records, oldest first, optionally limited
        to the most recent `limit`.
        """

        if limit is None:
            return list(self.execution_history)

        return self.execution_history[-limit:]

    # -- Persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return this memory as a JSON-serializable dictionary.
        """

        return {
            "conversation": [asdict(turn) for turn in self.conversation],
            "tasks": [asdict(task) for task in self.tasks],
            "project": dict(self.project),
            "execution_history": [asdict(record) for record in self.execution_history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        """
        Rebuild a Memory instance from a dictionary produced by
        `to_dict()`.
        """

        memory = cls()

        memory.conversation = [
            ConversationTurn(**turn) for turn in data.get("conversation", [])
        ]
        memory.tasks = [Task(**task) for task in data.get("tasks", [])]
        memory.project = dict(data.get("project", {}))
        memory.execution_history = [
            ExecutionRecord(**record) for record in data.get("execution_history", [])
        ]

        task_numbers = [
            int(task.id.split("-")[-1])
            for task in memory.tasks
            if task.id.startswith("task-") and task.id.split("-")[-1].isdigit()
        ]
        memory._task_counter = max(task_numbers, default=0)

        return memory

    def save(self, path: str | Path = DEFAULT_MEMORY_PATH) -> None:
        """
        Persist memory to a JSON file.
        """

        file_path = Path(path)

        if file_path.parent:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Saving memory to: %s", file_path)

        file_path.write_text(
            json.dumps(self.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path = DEFAULT_MEMORY_PATH) -> Memory:
        """
        Load memory from a JSON file.

        Returns an empty Memory if the file does not exist.
        """

        file_path = Path(path)

        if not file_path.exists():
            return cls()

        logger.info("Loading memory from: %s", file_path)

        data = json.loads(file_path.read_text(encoding="utf-8"))

        return cls.from_dict(data)
