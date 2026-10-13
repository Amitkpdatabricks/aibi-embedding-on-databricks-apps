import json

import pytest
from conftest import FakeResponse, FakeSession

from dbx_client import ApiError, Workspace
from embed_token import mint_embed_token, viewer_id


def oidc(kwargs):
    data = kwargs["data"]
    if data.get("scope") == "all-apis":
        return FakeResponse(body={"access_token": "sp-token", "expires_in": 3600})
    return FakeResponse(body={"access_token": "scoped-token", "expires_in": 3600})


def make_ws(routes, clock=lambda: 1000.0):
    return Workspace("adb-1.1.azuredatabricks.net", "cid", "secret", session=FakeSession(routes), clock=clock)


def test_viewer_id_normalises():
    a = viewer_id(" Alice@Example.com ")
    assert a == viewer_id("alice@example.com")
    assert len(a) == 32 and "@" not in a
    assert viewer_id("") == "anonymous"


def test_host_gets_https_prefix():
    assert make_ws({}).host == "https://adb-1.1.azuredatabricks.net"


def test_sp_token_cached_and_refreshed():
    now = [1000.0]
    ws = make_ws({("POST", "/oidc/v1/token"): oidc}, clock=lambda: now[0])
    assert ws.sp_token() == "sp-token"
    ws.sp_token()
    assert len(ws._session.calls) == 1
    now[0] += 3600
    ws.sp_token()
    assert len(ws._session.calls) == 2


def test_mint_flow_forwards_tokeninfo():
    tokeninfo = FakeResponse(body={"authorization_details": [{"type": "x"}], "custom_claim": "c1"})
    ws = make_ws({("POST", "/oidc/v1/token"): oidc, ("GET", "/published/tokeninfo"): tokeninfo})
    out = mint_embed_token(ws, "dash1", "viewer1")
    assert out == {"token": "scoped-token", "expires_in": 3600}
    _, url, kw = ws._session.calls[1]
    assert "/api/2.0/lakeview/dashboards/dash1/published/tokeninfo" in url
    assert kw["params"] == {"external_viewer_id": "viewer1"}
    step3 = ws._session.calls[2][2]["data"]
    assert step3["grant_type"] == "client_credentials"
    assert json.loads(step3["authorization_details"]) == [{"type": "x"}]
    assert step3["custom_claim"] == "c1"


def test_mint_sends_empty_external_value():
    tokeninfo = FakeResponse(body={"authorization_details": []})
    ws = make_ws({("POST", "/oidc/v1/token"): oidc, ("GET", "/published/tokeninfo"): tokeninfo})
    mint_embed_token(ws, "dash1", "viewer1", external_value="")
    assert ws._session.calls[1][2]["params"] == {"external_viewer_id": "viewer1", "external_value": ""}


def test_mint_error_names_step():
    ws = make_ws({("POST", "/oidc/v1/token"): oidc, ("GET", "/published/tokeninfo"): FakeResponse(403, {"message": "no"})})
    with pytest.raises(ApiError, match="step 2 tokeninfo failed \\(403\\)"):
        mint_embed_token(ws, "dash1", "viewer1")


def test_request_allow_404_returns_none():
    ws = make_ws({("POST", "/oidc/v1/token"): oidc, ("GET", "/get-status"): FakeResponse(404, {})})
    assert ws.request("GET", "/api/2.0/workspace/get-status", "find", allow_404=True) is None


def test_workspace_id_from_header(monkeypatch):
    monkeypatch.delenv("DATABRICKS_WORKSPACE_ID", raising=False)
    ws = make_ws({("POST", "/oidc/v1/token"): oidc,
                  ("GET", "/scim/v2/Me"): FakeResponse(body={}, headers={"x-databricks-org-id": "123"})})
    assert ws.workspace_id() == "123"
