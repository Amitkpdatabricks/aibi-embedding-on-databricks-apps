import json

import pytest
from conftest import FakeResponse, FakeSession

from dashboard_bootstrap import BootstrapError, ensure_dashboard, probe_samples_access
from dbx_client import Workspace

DEF = json.dumps({"datasets": [{"name": "sales", "queryLines": ["SELECT 1"]}], "pages": [{"name": "p"}]})
SP = {("POST", "/oidc/v1/token"): FakeResponse(body={"access_token": "t", "expires_in": 3600})}


def ws_with(routes):
    return Workspace("https://h", "app-sp", "s", session=FakeSession({**SP, **routes}))


@pytest.fixture
def definition(tmp_path):
    p = tmp_path / "d.lvdash.json"
    p.write_text(DEF)
    return p


def methods(ws):
    return [(m, u.replace("https://h", "")) for m, u, _ in ws._session.calls if "oidc" not in u]


def test_missing_warehouse(definition):
    with pytest.raises(BootstrapError, match="sql-warehouse"):
        ensure_dashboard(ws_with({}), "", "Demo", definition)


def test_creates_and_publishes_when_missing(definition):
    ws = ws_with({
        ("POST", "/workspace/mkdirs"): FakeResponse(body={}),
        ("GET", "/workspace/get-status"): FakeResponse(404, {}),
        ("POST", "/lakeview/dashboards/d1/published"): FakeResponse(body={}),
        ("POST", "/lakeview/dashboards"): FakeResponse(body={"dashboard_id": "d1"}),
    })
    assert ensure_dashboard(ws, "wh1", "Demo", definition) == "d1"
    create = [kw for m, u, kw in ws._session.calls if u.endswith("/api/2.0/lakeview/dashboards")][0]["json"]
    assert create["parent_path"] == "/Users/app-sp/aibi-embedding-demo"
    assert create["warehouse_id"] == "wh1" and create["serialized_dashboard"] == DEF
    publish = [kw for m, u, kw in ws._session.calls if u.endswith("/published")][0]["json"]
    assert publish == {"embed_credentials": True, "warehouse_id": "wh1"}


def test_reuses_existing_dashboard_without_patch(definition):
    ws = ws_with({
        ("POST", "/workspace/mkdirs"): FakeResponse(body={}),
        ("GET", "/workspace/get-status"): FakeResponse(body={"resource_id": "d1"}),
        ("POST", "/lakeview/dashboards/d1/published"): FakeResponse(body={}),
        ("GET", "/lakeview/dashboards/d1"): FakeResponse(body={"serialized_dashboard": DEF, "warehouse_id": "wh1"}),
    })
    assert ensure_dashboard(ws, "wh1", "Demo", definition) == "d1"
    assert ("PATCH", "/api/2.0/lakeview/dashboards/d1") not in methods(ws)
    assert ("POST", "/api/2.0/lakeview/dashboards") not in methods(ws)


def test_patches_when_definition_changed(definition):
    old = json.dumps({"datasets": [], "pages": []})
    ws = ws_with({
        ("POST", "/workspace/mkdirs"): FakeResponse(body={}),
        ("GET", "/workspace/get-status"): FakeResponse(body={"resource_id": "d1"}),
        ("POST", "/lakeview/dashboards/d1/published"): FakeResponse(body={}),
        ("GET", "/lakeview/dashboards/d1"): FakeResponse(body={"serialized_dashboard": old, "warehouse_id": "wh1"}),
        ("PATCH", "/lakeview/dashboards/d1"): FakeResponse(body={}),
    })
    ensure_dashboard(ws, "wh1", "Demo", definition)
    assert ("PATCH", "/api/2.0/lakeview/dashboards/d1") in methods(ws)


def test_probe_failure_explains_grants():
    ws = ws_with({("POST", "/sql/statements"): FakeResponse(body={"status": {"state": "FAILED", "error": {"message": "PERMISSION_DENIED"}}})})
    with pytest.raises(BootstrapError, match="GRANT"):
        probe_samples_access(ws, "wh1")


def test_probe_pending_is_ok():
    ws = ws_with({("POST", "/sql/statements"): FakeResponse(body={"status": {"state": "PENDING"}})})
    probe_samples_access(ws, "wh1")
