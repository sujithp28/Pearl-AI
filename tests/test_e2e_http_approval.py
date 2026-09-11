"""
The real HTTP/SSE flow, end to end, through the FastAPI app.

Everything here goes over the actual routes with a real TestClient: the
SSE run stream, the approval and rejection endpoints, and the tool
listing. Only the model is scripted (fixed plan JSON), which is this
project's integration-test convention — no network, no model download.

What these pin:

  * a run streams events and pauses without touching disk
  * approving writes, rejecting does not
  * a file modified between staging and approval is refused, with the
    conflicting paths named, and the user's content survives
  * a refused approval leaves the run resumable rather than wedged
  * /api/tools reports a risk tier for every tool
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.server import app, init_session


def _write_lf(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("PEARL_LLM_PROVIDER", "scripted")
    from src.config.settings import Settings

    monkeypatch.setattr(Settings, "LLM_PROVIDER", "scripted")
    init_session(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    # As a context manager, so the app's startup hook runs and registers
    # the event loop the SSE bridge posts events onto. Without it the
    # run worker cannot reach the loop at all.
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


def _script_plan(*steps) -> None:
    """Make the session's planner return this fixed plan for every call."""
    from src.api import server as srv

    srv._session.planner.client.generate_json = lambda prompt, cancel_check=None: {
        "steps": list(steps)
    }


def _script_reflection(verdict: str = "complete") -> None:
    from src.api import server as srv

    payload = json.dumps(
        {
            "status": verdict,
            "confidence": 1.0,
            "reasoning": "scripted",
            "missing_requirements": [],
            "should_replan": False,
        }
    )
    engine = getattr(srv._session, "_reflection_engine", None)
    if engine is not None and hasattr(engine, "client"):
        engine.client.generate = lambda *a, **k: payload


def _edit_plan(target: Path, search: str, replacement: str):
    return (
        {"tool": "read_file", "arguments": {"path": str(target)}},
        {
            "tool": "replace_in_file",
            "arguments": {
                "path": str(target),
                "search": search,
                "replacement": replacement,
            },
        },
    )


def _run(client: TestClient, prompt: str) -> list[dict]:
    """POST /api/run and drain the SSE stream into a list of events."""
    events: list[dict] = []
    with client.stream("POST", "/api/run", json={"prompt": prompt}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    return events


# ---------------------------------------------------------------------
# Approve / reject over the real routes
# ---------------------------------------------------------------------


def test_run_streams_and_pauses_without_writing(client, workspace):
    target = workspace / "app.py"
    _write_lf(target, "value = 1\n")

    _script_plan(*_edit_plan(target, "value = 1", "value = 2"))

    events = _run(client, "bump the value")

    assert events, "the SSE stream produced no events"
    # Nothing reached disk while the run waits for a human.
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_approve_over_http_writes_the_staged_change(client, workspace):
    target = workspace / "app.py"
    _write_lf(target, "value = 1\n")

    _script_plan(*_edit_plan(target, "value = 1", "value = 2"))
    _run(client, "bump the value")
    _script_reflection()

    resp = client.post("/api/approve")

    assert resp.status_code == 200
    assert "error" not in resp.json(), resp.json()
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_reject_over_http_leaves_the_file_alone(client, workspace):
    target = workspace / "app.py"
    _write_lf(target, "value = 1\n")

    _script_plan(*_edit_plan(target, "value = 1", "value = 2"))
    _run(client, "bump the value")

    resp = client.post("/api/reject")

    assert resp.status_code == 200
    assert target.read_text(encoding="utf-8") == "value = 1\n"


# ---------------------------------------------------------------------
# The race this hardening pass closed
# ---------------------------------------------------------------------


def test_approve_is_refused_when_the_file_changed_during_review(client, workspace):
    """
    The user reviews a diff, then saves their own edit to the same file
    before clicking approve. Approving must not overwrite them.
    """
    target = workspace / "app.py"
    _write_lf(target, "value = 1\n")

    _script_plan(*_edit_plan(target, "value = 1", "value = 2"))
    _run(client, "bump the value")

    # The user saves while the diff is on screen.
    _write_lf(target, "value = 99  # hand-edited\n")

    body = client.post("/api/approve").json()

    assert body.get("stale") is True
    assert str(target) in body.get("conflicts", [])
    assert "changed on disk" in body.get("error", "")
    # Their content is intact.
    assert target.read_text(encoding="utf-8") == "value = 99  # hand-edited\n"


def test_the_run_is_still_rejectable_after_a_refused_approval(client, workspace):
    """
    A refused approval must leave the run resumable, not wedged: the
    apply is aborted before the paused state is consumed.
    """
    target = workspace / "app.py"
    _write_lf(target, "value = 1\n")

    _script_plan(*_edit_plan(target, "value = 1", "value = 2"))
    _run(client, "bump the value")
    _write_lf(target, "mine\n")

    assert client.post("/api/approve").json().get("stale") is True

    rejected = client.post("/api/reject").json()

    assert "error" not in rejected, rejected
    assert target.read_text(encoding="utf-8") == "mine\n"


# ---------------------------------------------------------------------
# Risk tiers over the API
# ---------------------------------------------------------------------


def test_tools_endpoint_reports_a_risk_tier_for_every_tool(client):
    body = client.get("/api/tools").json()

    assert body["count"] > 0
    for entry in body["tools"]:
        assert entry["risk_level"] in {"safe", "staged", "dangerous"}, entry

    by_name = {t["name"]: t["risk_level"] for t in body["tools"]}
    assert by_name["read_file"] == "safe"
    assert by_name["write_file"] == "staged"
    assert by_name["execute_shell"] == "dangerous"
