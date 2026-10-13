import pytest
from fastapi.testclient import TestClient

import requests

import app as app_module

REAL_BOOTSTRAP = app_module.bootstrap
from dbx_client import ApiError


class StubWs:
    host = "https://h"

    def workspace_id(self):
        return "123"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "bootstrap", lambda: None)
    app_module.state.ws, app_module.state.dashboard_id = StubWs(), "d1"
    app_module.state.error, app_module.state.preparing = None, False
    return TestClient(app_module.app)


def test_embed_config_returns_token(client, monkeypatch):
    seen = {}

    def fake_mint(ws, dashboard_id, external_viewer_id, external_value=None):
        seen.update(dashboard_id=dashboard_id, viewer=external_viewer_id, value=external_value)
        return {"token": "tok", "expires_in": 3600}

    monkeypatch.setattr(app_module, "mint_embed_token", fake_mint)
    r = client.get("/api/embed-config", headers={"x-forwarded-email": "Alice@Example.com"})
    assert r.status_code == 200
    assert r.json() == {"workspace_url": "https://h", "workspace_id": "123", "dashboard_id": "d1",
                        "token": "tok", "expires_in": 3600, "viewer": "Alice@Example.com"}
    assert seen["viewer"] == app_module.viewer_id("alice@example.com") and seen["value"] is None


def test_embed_config_503_on_bootstrap_error(client):
    app_module.state.error = "No SQL warehouse attached"
    r = client.get("/api/embed-config")
    assert r.status_code == 503 and "warehouse" in r.json()["error"]


def test_embed_config_503_while_preparing(client):
    app_module.state.dashboard_id, app_module.state.preparing = None, True
    assert client.get("/api/embed-config").status_code == 503


def test_embed_config_502_on_mint_error(client, monkeypatch):
    def boom(*a, **k):
        raise ApiError("step 2 tokeninfo", 403, "denied")

    monkeypatch.setattr(app_module, "mint_embed_token", boom)
    r = client.get("/api/embed-config")
    assert r.status_code == 502 and "step 2" in r.json()["error"]


def test_health_reports_status(client):
    assert client.get("/api/health").json()["status"] == "ok"
    app_module.state.error = "x"
    assert client.get("/api/health").json()["status"] == "error"


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "aibi-client" in r.text


def test_bootstrap_endpoint_not_exposed(client):
    assert client.post("/api/bootstrap").status_code in (404, 405)


def test_unexpected_bootstrap_error_is_reported(monkeypatch):
    app_module.state.ws, app_module.state.dashboard_id = StubWs(), None

    def broken(*a, **k):
        raise requests.ConnectionError("network down")

    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "")
    monkeypatch.setattr(app_module, "ensure_dashboard", broken)
    REAL_BOOTSTRAP()
    assert "Unexpected bootstrap error" in app_module.state.error
    assert app_module.state.preparing is False


def test_missing_env_is_reported(monkeypatch):
    app_module.state.ws = None

    def no_env():
        raise KeyError("DATABRICKS_HOST")

    monkeypatch.setattr(app_module.Workspace, "from_env", staticmethod(no_env))
    REAL_BOOTSTRAP()
    assert "Missing environment variable" in app_module.state.error


def test_concurrent_bootstrap_is_skipped(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "ensure_dashboard", lambda *a, **k: calls.append(1) or "dx")
    app_module._bootstrap_lock.acquire()
    try:
        REAL_BOOTSTRAP()
    finally:
        app_module._bootstrap_lock.release()
    assert calls == []
