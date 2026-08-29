"""
Tests for chat-mode capability grounding (`src/prompts/system.py`).

The bug this guards against: Pearl's chat model had no idea it was
part of a coding agent, so it answered "write hello world" by showing
code and narrating having saved it — reading to the user as a claim
that a file was created when nothing had happened. Asked directly
("did u create a file"), it deflected.

The grounding prompt must reach chat, must NOT reach planning (whose
output is parsed as JSON and must stay unprimed), and must contain no
personality/tone content — that lives in `src/personality/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import Settings
from src.prompts.system import build_chat_system_prompt


@pytest.fixture(autouse=True)
def _force_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(Settings, "OPENAI_API_KEY", "dummy-test-key")

# ---------------------------------------------------------------------
# The prompt itself
# ---------------------------------------------------------------------


def test_prompt_substitutes_the_workspace_root():
    prompt = build_chat_system_prompt("/home/sujith/pearl-agent")

    assert "/home/sujith/pearl-agent" in prompt
    assert "{workspace_root}" not in prompt


def test_prompt_establishes_pearls_identity_positively():
    """
    Framed as what Pearl *is*, not as a list of things not to say.

    Live-tested against the real 3B model: an earlier draft that said
    'never describe yourself as ChatGPT / an AI language model'
    produced replies opening with "as an AI language model, I don't
    have a physical workspace" — the model appeared to echo the very
    phrase the prompt named. Negative instructions are weak on small
    models and can prime the thing they forbid, so identity is
    asserted rather than prohibited.
    """

    prompt = build_chat_system_prompt("/tmp/x")

    assert "You are Pearl" in prompt
    # No forbidden-phrase list for the model to echo back.
    for primed in ["ChatGPT", "AI language model"]:
        assert primed not in prompt, f"naming {primed!r} risks the model repeating it"


def test_prompt_asserts_pearl_has_a_real_workspace_and_tools():
    # The failure mode this prevents: Pearl claiming it "has no
    # workspace or storage capabilities", which is simply false.
    prompt = build_chat_system_prompt("/tmp/x").lower()

    assert "real workspace" in prompt
    assert "real tools" in prompt


def test_prompt_requires_truthful_reporting_of_actions():
    prompt = build_chat_system_prompt("/tmp/x").lower()

    assert "truthfully" in prompt
    for verb in ["created", "written", "saved", "edited", "deleted"]:
        assert verb in prompt


def test_prompt_tells_the_model_to_answer_honestly_when_asked():
    # The exact failure from the bug report: "did u create a file"
    # was answered with a deflection instead of "no".
    prompt = build_chat_system_prompt("/tmp/x").lower()

    assert "if asked whether you did something" in prompt


def test_prompt_carries_no_personality_or_tone_content():
    """
    Capability grounding only. Tone/emoji are `src/personality/`'s
    job, and the personality spec explicitly forbids personality from
    reaching LLM prompts.
    """

    prompt = build_chat_system_prompt("/tmp/x").lower()

    for tone_word in ["witty", "playful", "cheeky", "savage", "humor", "joke", "emoji"]:
        assert tone_word not in prompt, (
            f"grounding prompt must not carry tone content ({tone_word!r})"
        )


def test_prompt_is_stable_across_calls():
    assert build_chat_system_prompt("/a") == build_chat_system_prompt("/a")


def test_prompt_survives_braces_in_the_workspace_path():
    # `.replace()` rather than `.format()` means a brace anywhere can
    # never raise — including one in the substituted value itself.
    prompt = build_chat_system_prompt("/tmp/weird{dir}")

    assert "/tmp/weird{dir}" in prompt


# ---------------------------------------------------------------------
# Reaches chat, never reaches planning
# ---------------------------------------------------------------------


def test_chat_sends_the_grounding_prompt_as_a_system_message(monkeypatch):
    from src.llm.client import LLMClient

    client = LLMClient()
    captured: list[list[dict]] = []

    def fake_create(**kwargs):
        captured.append(kwargs["messages"])
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(client.provider.client.chat.completions, "create", fake_create)

    client.generate("hi", system="GROUNDING")

    assert captured[0][0] == {"role": "system", "content": "GROUNDING"}


def test_system_message_precedes_history_and_prompt(monkeypatch):
    from src.llm.client import LLMClient

    client = LLMClient()
    captured: list[list[dict]] = []

    def fake_create(**kwargs):
        captured.append(kwargs["messages"])
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(client.provider.client.chat.completions, "create", fake_create)

    client.generate(
        "now",
        history=[{"role": "user", "content": "before"}],
        system="GROUNDING",
    )

    assert [m["role"] for m in captured[0]] == ["system", "user", "user"]
    assert captured[0][0]["content"] == "GROUNDING"
    assert captured[0][1]["content"] == "before"
    assert captured[0][2]["content"] == "now"


def test_generate_sends_no_system_message_when_not_asked(monkeypatch):
    from src.llm.client import LLMClient

    client = LLMClient()
    captured: list[list[dict]] = []

    def fake_create(**kwargs):
        captured.append(kwargs["messages"])
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(client.provider.client.chat.completions, "create", fake_create)

    client.generate("hi")

    assert all(m["role"] != "system" for m in captured[0])


def test_planning_is_never_given_the_grounding_prompt(monkeypatch):
    """
    Planning output is parsed as JSON. Priming it with prose written
    for a human ("you may show the code or explain the approach") is
    exactly the kind of thing that breaks structured output.
    """

    from src.llm.client import LLMClient

    client = LLMClient()
    captured: list[list[dict]] = []

    def fake_create(**kwargs):
        captured.append(kwargs["messages"])
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"steps": []}'))]
        )

    monkeypatch.setattr(client.provider.client.chat.completions, "create", fake_create)

    client.generate_json("plan something")

    assert all(m["role"] != "system" for m in captured[0])


# ---------------------------------------------------------------------
# Wired into the real chat entry points
# ---------------------------------------------------------------------


def test_mcp_chat_passes_the_grounding_prompt():
    from src.mcp.protocol import JsonRpcRequest
    from src.mcp.server import MCPServer
    from src.tools.registry import ToolRegistry

    class RecordingLLM:
        def __init__(self):
            self.systems = []

        def generate(self, prompt, history=None, system=None):
            self.systems.append(system)
            return "reply"

        def generate_stream(self, prompt, history=None, system=None):
            yield self.generate(prompt, history=history, system=system)

    llm = RecordingLLM()
    server = MCPServer(ToolRegistry(), llm=llm)

    server.handle_request(
        JsonRpcRequest(method="pearl/chat", id=1, params={"message": "hi"})
    )

    assert llm.systems[0] is not None
    assert "You are Pearl" in llm.systems[0]


def test_mcp_chat_grounding_names_the_real_workspace(tmp_path, monkeypatch):
    from src.mcp.protocol import JsonRpcRequest
    from src.mcp.server import MCPServer
    from src.tools.registry import ToolRegistry

    class RecordingLLM:
        def __init__(self):
            self.systems = []

        def generate(self, prompt, history=None, system=None):
            self.systems.append(system)
            return "reply"

        def generate_stream(self, prompt, history=None, system=None):
            yield self.generate(prompt, history=history, system=system)

    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    llm = RecordingLLM()
    server = MCPServer(ToolRegistry(), llm=llm)

    server.handle_request(
        JsonRpcRequest(method="pearl/chat", id=1, params={"message": "hi"})
    )

    assert str(tmp_path.resolve()) in llm.systems[0]


def test_pearl_agent_chat_passes_the_grounding_prompt(monkeypatch):
    from src.agent.agent import PearlAgent
    from src.tools.registry import ToolRegistry

    agent = PearlAgent(ToolRegistry())

    captured: list[str | None] = []

    def fake_generate(prompt, history=None, system=None, **kwargs):
        captured.append(system)
        return "reply"

    monkeypatch.setattr(agent.llm, "generate", fake_generate)

    agent.chat("hi")

    assert captured[0] is not None
    assert "You are Pearl" in captured[0]
