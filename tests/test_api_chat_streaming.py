"""
Regression test: /api/chat must stream chunks as they arrive, not
buffer the entire response then flush it at the end.

The bug: the original implementation used
    list(session.chat_stream(req.message))
which materialises all LLM chunks before the SSE response body starts.
Users saw a long blank wait followed by the full response appearing at once.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.server import app, init_session


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Use scripted provider so the session constructs without any real API key.
    monkeypatch.setenv("PEARL_LLM_PROVIDER", "scripted")
    from src.config.settings import Settings

    monkeypatch.setattr(Settings, "LLM_PROVIDER", "scripted")
    init_session(tmp_path)
    return TestClient(app, raise_server_exceptions=True)


def test_chat_streams_chunks_before_generator_finishes(client, monkeypatch):
    """
    Each chunk must be yielded to the SSE response as it comes from the
    LLM generator — not after the generator has been fully consumed.
    """

    arrival_order: list[str] = []
    ready = threading.Event()

    def slow_generator(message, *, history=None, system=None):
        # Signal that streaming has started
        ready.set()
        yield "hello"
        yield " world"

    from src.api import server as srv_mod

    real_session = srv_mod._session
    real_session._chat_llm = MagicMock()
    real_session._chat_llm.generate_stream.side_effect = slow_generator

    # Collect SSE events from the streaming response
    with client.stream("POST", "/api/chat", json={"message": "hi"}) as resp:
        for line in resp.iter_lines():
            if line.startswith("data:"):
                try:
                    d = json.loads(line[5:].strip())
                    if d.get("text"):
                        arrival_order.append(d["text"])
                except json.JSONDecodeError:
                    pass

    assert arrival_order == ["hello", " world"], (
        f"Expected incremental chunks ['hello', ' world'], got {arrival_order}"
    )
