import json

import pytest
from conftest import FakeResponse, FakeSession

from dbx_client import Workspace
from rls import NO_ACCESS, load_config, resolve_external_value, resolve_scope


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "regions.json"
    p.write_text(json.dumps({"prefix": "region-", "adminGroups": ["region-admin"], "groups": {"legacy-emea": "EUROPE"}}))
    return load_config(p, env={})


def test_single_region(cfg):
    assert resolve_scope(["users", "region-europe"], cfg) == "EUROPE"


def test_multi_region_sorted(cfg):
    assert resolve_scope(["region-europe", "region-asia"], cfg) == "ASIA,EUROPE"


def test_multiword_region(cfg):
    assert resolve_scope(["region-middle-east"], cfg) == "MIDDLE EAST"


def test_case_insensitive(cfg):
    assert resolve_scope(["Region-Europe"], cfg) == "EUROPE"


def test_admin_wins_over_regions(cfg):
    assert resolve_scope(["region-europe", "region-admin"], cfg) == ""


def test_no_region_fails_closed(cfg):
    assert resolve_scope(["users", "admins"], cfg) == NO_ACCESS
    assert resolve_scope([], cfg) == NO_ACCESS


def test_explicit_group_map(cfg):
    assert resolve_scope(["legacy-emea"], cfg) == "EUROPE"


def test_env_overrides(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"prefix": "region-", "adminGroups": ["region-admin"], "groups": {}}))
    cfg = load_config(p, env={"REGION_GROUP_PREFIX": "geo_", "ADMIN_GROUPS": "boss", "GROUP_REGION_MAP": "ops-apac:ASIA"})
    assert resolve_scope(["geo_africa"], cfg) == "AFRICA"
    assert resolve_scope(["boss"], cfg) == ""
    assert resolve_scope(["ops-apac"], cfg) == "ASIA"


def ws_with(routes):
    sp = {("POST", "/oidc/v1/token"): FakeResponse(body={"access_token": "t", "expires_in": 3600})}
    return Workspace("https://h", "sp", "s", session=FakeSession({**sp, **routes}))


def test_resolves_from_me_with_user_token(cfg):
    ws = ws_with({("GET", "/scim/v2/Me"): FakeResponse(body={"groups": [{"display": "region-asia"}]})})
    value, info = resolve_external_value(ws, {"x-forwarded-access-token": "user-tok"}, cfg)
    assert value == "ASIA" and info["groups"] == ["region-asia"]
    _, _, kw = ws._session.calls[-1]
    assert kw["headers"]["Authorization"] == "Bearer user-tok"


def test_missing_user_token_fails_closed(cfg):
    value, info = resolve_external_value(ws_with({}), {}, cfg)
    assert value == NO_ACCESS and "User authorization" in info["reason"]


def test_me_error_fails_closed(cfg):
    ws = ws_with({("GET", "/scim/v2/Me"): FakeResponse(401, {"message": "bad token"})})
    value, info = resolve_external_value(ws, {"x-forwarded-access-token": "x"}, cfg)
    assert value == NO_ACCESS and "401" in info["reason"]


@pytest.mark.parametrize("groups", [["region--"], ["region-_"], ["region- "], ["blank-map"]])
def test_non_admin_never_gets_all_rows(tmp_path, groups):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"prefix": "region-", "adminGroups": ["region-admin"], "groups": {"blank-map": ""}}))
    assert resolve_scope(groups, load_config(p, env={})) == NO_ACCESS
