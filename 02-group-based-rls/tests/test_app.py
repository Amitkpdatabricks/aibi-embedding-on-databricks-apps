import pytest
from fastapi.testclient import TestClient

import requests

import app as app_module

REAL_BOOTSTRAP = app_module.bootstrap


class StubWs:
    host = "https://h"

    def workspace_id(self):
        return "123"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "bootstrap", lambda: None)
    app_module.state.ws, app_module.state.dashboard_id = StubWs(), "d2"
    app_module.state.error, app_module.state.preparing = None, False
    return TestClient(app_module.app)


def test_scope_passed_to_token(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(app_module, "resolve_external_value",
                        lambda ws, headers, cfg: ("EUROPE", {"groups": ["region-europe"], "scope": "EUROPE", "reason": ""}))
    monkeypatch.setattr(app_module, "mint_embed_token",
                        lambda ws, d, v, external_value=None: seen.update(value=external_value) or {"token": "t", "expires_in": 3600})
    r = client.get("/api/embed-config?external_value=ASIA", headers={"x-forwarded-email": "a@b.com"})
    assert r.status_code == 200
    assert seen["value"] == "EUROPE"
    assert r.json()["scope"]["scope"] == "EUROPE"


def test_no_user_token_gives_no_access(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(app_module, "mint_embed_token",
                        lambda ws, d, v, external_value=None: seen.update(value=external_value) or {"token": "t", "expires_in": 3600})
    r = client.get("/api/embed-config")
    assert seen["value"] == "__no_access__"
    assert "User authorization" in r.json()["scope"]["reason"]


def test_index_has_scope_banner(client):
    assert 'id="scope"' in client.get("/").text


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
