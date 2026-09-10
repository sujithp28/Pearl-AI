"""
The browser must be able to fetch the UI's ES modules.

`index.html` stopped being the whole front end when the browser logic
moved into `pearl_ui/js/`. It now loads `/js/main.js`, so a server that
serves the page but not the modules renders a blank shell with no error
anywhere on the Python side.

The mount also has to stay out of the API's way: a static mount at the
root would answer before every `/api/*` route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server(monkeypatch, tmp_path):
    from src.api import server as srv

    monkeypatch.setattr(srv, "_session", None)
    monkeypatch.setattr(srv, "_workspace", None)
    srv._registry.clear()
    srv.init_session(tmp_path)
    return srv


class TestModuleServing:
    def test_entry_module_is_served(self, server):
        with TestClient(server.app) as client:
            r = client.get("/js/main.js")

        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]

    def test_every_imported_module_is_reachable(self, server):
        """
        A missing module is a silent failure in the browser, so the
        whole set is checked rather than just the entry point.
        """
        modules = [
            "api.js",
            "checkpoints.js",
            "code.js",
            "conversation.js",
            "format.js",
            "main.js",
            "markdown.js",
            "settings.js",
            "state.js",
        ]

        with TestClient(server.app) as client:
            missing = [m for m in modules if client.get(f"/js/{m}").status_code != 200]

        assert missing == []

    def test_index_still_served_at_root(self, server):
        with TestClient(server.app) as client:
            r = client.get("/")

        assert r.status_code == 200
        assert "/js/main.js" in r.text

    def test_the_mount_does_not_shadow_the_api(self, server):
        with TestClient(server.app) as client:
            assert client.get("/api/status").status_code == 200

    def test_a_missing_module_is_a_404_not_the_index_page(self, server):
        with TestClient(server.app) as client:
            r = client.get("/js/does-not-exist.js")

        assert r.status_code == 404
