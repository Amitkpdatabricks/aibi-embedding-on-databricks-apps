# AI/BI Embedding on Databricks Apps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public repo with two Git-deployable Databricks Apps: Part 1 embeds a self-provisioned AI/BI dashboard with external-embed tokens, Part 2 adds group-based row filtering.

**Architecture:** Each part is a self-contained FastAPI app folder (Git source code path). On startup the app, as its own service principal, creates/publishes a dashboard on `samples.tpch` from a bundled `.lvdash.json`. Per page load it mints a scoped embed token (3-step OAuth); the browser renders it with `@databricks/aibi-client` from a CDN. Part 2 resolves `external_value` from the viewer's groups via SCIM `/Me` with the forwarded user token.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, requests, pytest, httpx (TestClient); `@databricks/aibi-client@1.2.0` via jsDelivr ESM; Databricks REST (Lakeview, Workspace, SQL Statements, OIDC, SCIM).

**Spec:** `docs/superpowers/specs/2026-10-12-aibi-embedding-on-databricks-apps-design.md`

## Global Constraints

- App folders: `01-external-embedding/` and `02-group-based-rls/`; each self-contained (no imports outside the folder).
- Warehouse resource key: `sql-warehouse`, mapped to env `DATABRICKS_WAREHOUSE_ID` via `valueFrom`.
- Dashboard folder: `/Users/<DATABRICKS_CLIENT_ID>/aibi-embedding-demo`; names `Sample Sales - External Embedding` (Part 1) and `Sample Sales - Group-Based RLS` (Part 2).
- Data: `samples.tpch` only. Region values: `AFRICA`, `AMERICA`, `ASIA`, `EUROPE`, `MIDDLE EAST`.
- `external_viewer_id` = first 32 hex of `sha256(lower(strip(email)))`; `anonymous` if no email.
- Part 2 scope: admin -> `""`; regions -> sorted comma list; none/failure -> `__no_access__`. Never empty for non-admins. Client input never affects scope.
- Group convention: prefix `region-`, admin group `region-admin`; suffix hyphens/underscores become spaces, uppercased (`region-middle-east` -> `MIDDLE EAST`).
- RLS predicate (exact): `nullif(__aibi_external_value, '') IS NULL OR array_contains(split(__aibi_external_value, ','), upper(r.r_name))`.
- No em dashes in docs. Commits end with `Co-authored-by: Isaac <no-reply@databricks.com>`.

## Review Focus

1. Email casing/whitespace variants must map to the same `external_viewer_id` (Task 1 test `test_viewer_id_normalises`).
2. A viewer in both `region-admin` and a region group sees all rows, not one region (Task 5 test `test_admin_wins_over_regions`).
3. `region-middle-east` must match `MIDDLE EAST` (with a space) in the data (Task 5 test `test_multiword_region`).
4. Restarting or redeploying the app must not create a second dashboard (Task 3 test `test_reuses_existing_dashboard_without_patch`).
5. Missing forwarded user token (user authorization not enabled) must fail closed with an explanatory reason, not error or show all rows (Task 5 test `test_missing_user_token_fails_closed`).

---

### Task 0: Repo scaffolding

**Files:**
- Create: `.gitignore`, `LICENSE`, `requirements-dev.txt`, `README.md` (stub, completed in Task 7)

- [ ] **Step 1: Create files**

`.gitignore`:
```
__pycache__/
.venv/
.pytest_cache/
*.pyc
.env
```

`requirements-dev.txt`:
```
fastapi>=0.110
uvicorn[standard]>=0.29
requests>=2.31
pytest>=8
httpx>=0.27
```

`LICENSE`: Apache License 2.0 text (copy from https://www.apache.org/licenses/LICENSE-2.0.txt), copyright `2026 Amit Kumar Panda`.

`README.md`: `# AI/BI Dashboard Embedding on Databricks Apps` plus one line "Work in progress".

- [ ] **Step 2: Create venv**

Run: `uv venv -p 3.11 .venv && uv pip install -p .venv -r requirements-dev.txt`
Expected: installs without error.

- [ ] **Step 3: Commit** `git add -A && git commit -m "chore: scaffold repo"`

---

### Task 1: Part 1 REST client and embed token

**Files:**
- Create: `01-external-embedding/dbx_client.py`, `01-external-embedding/embed_token.py`
- Test: `01-external-embedding/tests/conftest.py`, `01-external-embedding/tests/test_embed_token.py`

**Interfaces:**
- Produces: `ApiError(step, status, body)`; `Workspace(host, client_id, client_secret, session=None, clock=time.time)` with `.host`, `.client_id`, `.from_env()`, `.oidc_token(data, step) -> dict`, `.sp_token() -> str`, `.request(method, path, step, token=None, allow_404=False, **kw) -> Response | None`, `.workspace_id() -> str`; `viewer_id(email) -> str`; `mint_embed_token(ws, dashboard_id, external_viewer_id, external_value=None) -> {"token": str, "expires_in": int}`.

- [ ] **Step 1: Write test helpers** `tests/conftest.py`:

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    """Routes are (METHOD, url_fragment) -> FakeResponse or callable(kwargs) -> FakeResponse."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (m, fragment), resp in self.routes.items():
            if m == method and fragment in url:
                return resp(kwargs) if callable(resp) else resp
        raise AssertionError(f"unexpected call {method} {url}")
```

- [ ] **Step 2: Write failing tests** `tests/test_embed_token.py`:

```python
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
```

- [ ] **Step 3: Run** `cd 01-external-embedding && ../.venv/bin/pytest -q` Expected: FAIL (modules missing).

- [ ] **Step 4: Implement** `dbx_client.py`:

```python
"""Minimal Databricks REST client that authenticates as the app service principal.

On Databricks Apps the runtime injects DATABRICKS_HOST, DATABRICKS_CLIENT_ID and
DATABRICKS_CLIENT_SECRET for the app's own service principal.
"""

import base64
import os
import time

import requests


class ApiError(RuntimeError):
    def __init__(self, step, status, body):
        super().__init__(f"{step} failed ({status}): {body[:500]}")
        self.step = step
        self.status = status
        self.body = body


class Workspace:
    def __init__(self, host, client_id, client_secret, session=None, clock=time.time):
        self.host = (host if host.startswith("http") else f"https://{host}").rstrip("/")
        self.client_id = client_id
        self._secret = client_secret
        self._session = session or requests.Session()
        self._clock = clock
        self._token = None
        self._token_expires_at = 0.0
        self._workspace_id = os.environ.get("DATABRICKS_WORKSPACE_ID", "")

    @classmethod
    def from_env(cls):
        return cls(
            os.environ["DATABRICKS_HOST"],
            os.environ["DATABRICKS_CLIENT_ID"],
            os.environ["DATABRICKS_CLIENT_SECRET"],
        )

    def _basic_auth(self):
        raw = f"{self.client_id}:{self._secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def oidc_token(self, data, step):
        resp = self._session.request(
            "POST",
            f"{self.host}/oidc/v1/token",
            headers={"Authorization": self._basic_auth(), "Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=30,
        )
        if resp.status_code != 200:
            raise ApiError(step, resp.status_code, resp.text)
        return resp.json()

    def sp_token(self):
        # Reuse the service principal token until a minute before it expires.
        if self._token and self._clock() < self._token_expires_at - 60:
            return self._token
        body = self.oidc_token({"grant_type": "client_credentials", "scope": "all-apis"}, "step 1 SP token")
        self._token = body["access_token"]
        self._token_expires_at = self._clock() + int(body.get("expires_in", 3600))
        return self._token

    def request(self, method, path, step, token=None, allow_404=False, **kwargs):
        resp = self._session.request(
            method,
            f"{self.host}{path}",
            headers={"Authorization": f"Bearer {token or self.sp_token()}"},
            timeout=60,
            **kwargs,
        )
        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code >= 400:
            raise ApiError(step, resp.status_code, resp.text)
        return resp

    def workspace_id(self):
        # The embed SDK needs the numeric workspace id; every workspace API response carries it.
        if not self._workspace_id:
            resp = self.request("GET", "/api/2.0/preview/scim/v2/Me", "workspace id lookup")
            self._workspace_id = resp.headers.get("x-databricks-org-id", "")
        return self._workspace_id
```

`embed_token.py`:

```python
"""AI/BI external-embed token exchange.

Docs: https://docs.databricks.com/aws/en/dashboards/embedding/external-embed

1. POST /oidc/v1/token (client_credentials, all-apis)           -> SP token
2. GET  /api/2.0/lakeview/dashboards/{id}/published/tokeninfo   -> authorization_details
3. POST /oidc/v1/token (client_credentials + authorization_details) -> scoped embed token
"""

import hashlib
import json


def viewer_id(email):
    """Stable, non-PII viewer id for audit logs (docs: external_viewer_id must not contain PII)."""
    normalised = (email or "").strip().lower()
    if not normalised:
        return "anonymous"
    return hashlib.sha256(normalised.encode()).hexdigest()[:32]


def mint_embed_token(ws, dashboard_id, external_viewer_id, external_value=None):
    params = {"external_viewer_id": external_viewer_id}
    if external_value is not None:
        params["external_value"] = external_value
    info = ws.request(
        "GET",
        f"/api/2.0/lakeview/dashboards/{dashboard_id}/published/tokeninfo",
        "step 2 tokeninfo",
        params=params,
    ).json()
    authorization_details = info.pop("authorization_details", None)
    data = {**info, "grant_type": "client_credentials", "authorization_details": json.dumps(authorization_details)}
    body = ws.oidc_token(data, "step 3 scoped token")
    return {"token": body["access_token"], "expires_in": int(body.get("expires_in", 3600))}
```

- [ ] **Step 5: Run** `../.venv/bin/pytest -q` Expected: 8 passed.
- [ ] **Step 6: Commit** `git add 01-external-embedding && git commit -m "feat(part1): REST client and embed token exchange"`

---

### Task 2: Sample dashboard definitions

**Files:**
- Create: `01-external-embedding/dashboard/sample.lvdash.json`, `02-group-based-rls/dashboard/sample_rls.lvdash.json` (generated once, committed)
- Test: `01-external-embedding/tests/test_dashboard_definition.py`

**Interfaces:**
- Produces: two `.lvdash.json` files with one dataset `sales` and these columns: `order_key, order_date, order_status, revenue, customer_key, nation, region`.

- [ ] **Step 1: Generate** with the Lakeview builder (one-off script in `/tmp`, not committed):

```python
# /tmp/build_dash.py  run: PYTHONPATH=<lakeview-builder-dir> python3 /tmp/build_dash.py <repo>
import sys
from lakeview_builder import LakeviewDashboard

BASE = """SELECT o.o_orderkey AS order_key, o.o_orderdate AS order_date, o.o_orderstatus AS order_status,
       o.o_totalprice AS revenue, c.c_custkey AS customer_key, n.n_name AS nation, r.r_name AS region
FROM samples.tpch.orders o
JOIN samples.tpch.customer c ON o.o_custkey = c.c_custkey
JOIN samples.tpch.nation n ON c.c_nationkey = n.n_nationkey
JOIN samples.tpch.region r ON n.n_regionkey = r.r_regionkey"""
RLS = ("\nWHERE nullif(__aibi_external_value, '') IS NULL"
       "\n   OR array_contains(split(__aibi_external_value, ','), upper(r.r_name))")

def build(title, query):
    d = LakeviewDashboard(title)
    d.add_dataset("sales", "Sales (samples.tpch)", query)
    d.add_page("Overview")
    d.add_filter_dropdown("sales", "region", title="Region", multi_select=True, position={"x": 0, "y": 0, "width": 2, "height": 2})
    d.add_date_filter("sales", "order_date", title="Order date", position={"x": 2, "y": 0, "width": 2, "height": 2})
    d.add_counter("sales", "revenue", "SUM", title="Total revenue", position={"x": 0, "y": 2, "width": 3, "height": 3})
    d.add_counter("sales", "order_key", "COUNT", title="Orders", position={"x": 3, "y": 2, "width": 3, "height": 3})
    d.add_bar_chart("sales", "region", "revenue", "SUM", title="Revenue by region", position={"x": 0, "y": 5, "width": 3, "height": 6})
    d.add_line_chart("sales", "order_date", "revenue", "SUM", time_grain="MONTH", title="Revenue by month", position={"x": 3, "y": 5, "width": 3, "height": 6})
    d.add_bar_chart("sales", "nation", "revenue", "SUM", title="Revenue by nation", sort_descending=True, position={"x": 0, "y": 11, "width": 4, "height": 6})
    d.add_pie_chart("sales", "order_key", "order_status", "COUNT", title="Order status", position={"x": 4, "y": 11, "width": 2, "height": 6})
    return d.to_json()

repo = sys.argv[1]
open(f"{repo}/01-external-embedding/dashboard/sample.lvdash.json", "w").write(build("Sample Sales - External Embedding", BASE))
open(f"{repo}/02-group-based-rls/dashboard/sample_rls.lvdash.json", "w").write(build("Sample Sales - Group-Based RLS", BASE + RLS))
```

If a builder signature differs, adapt the call to the builder's actual signature; the output requirements above are what matter.

- [ ] **Step 2: Test** `tests/test_dashboard_definition.py`:

```python
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PART1 = ROOT / "01-external-embedding/dashboard/sample.lvdash.json"
PART2 = ROOT / "02-group-based-rls/dashboard/sample_rls.lvdash.json"
PREDICATE = "array_contains(split(__aibi_external_value, ','), upper(r.r_name))"


def queries(path):
    d = json.loads(path.read_text())
    assert d["pages"], "dashboard has no pages"
    return ["\n".join(ds.get("queryLines", [ds.get("query", "")])) for ds in d["datasets"]]


def test_part1_reads_samples_without_rls():
    qs = queries(PART1)
    assert all("samples.tpch" in q for q in qs)
    assert not any("__aibi_external_value" in q for q in qs)


def test_part2_every_dataset_has_rls_predicate():
    qs = queries(PART2)
    assert qs and all(PREDICATE in q and "nullif(__aibi_external_value, '') IS NULL" in q for q in qs)
```

- [ ] **Step 3: Run** `cd 01-external-embedding && ../.venv/bin/pytest -q tests/test_dashboard_definition.py` Expected: 2 passed.
- [ ] **Step 4: Commit** `git add -A && git commit -m "feat: sample dashboards on samples.tpch"`

---

### Task 3: Part 1 dashboard bootstrap

**Files:**
- Create: `01-external-embedding/dashboard_bootstrap.py`
- Test: `01-external-embedding/tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `Workspace.request`, `Workspace.client_id`.
- Produces: `BootstrapError`; `FOLDER = "aibi-embedding-demo"`; `probe_samples_access(ws, warehouse_id) -> None`; `ensure_dashboard(ws, warehouse_id, display_name, definition_file) -> str`.

- [ ] **Step 1: Failing tests**:

```python
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
```

Note: FakeSession matches routes in insertion order by fragment, so the more specific `/published` and `/d1` routes are listed before the bare `/lakeview/dashboards` route.

- [ ] **Step 2: Run** `../.venv/bin/pytest -q tests/test_bootstrap.py` Expected: FAIL (module missing).

- [ ] **Step 3: Implement** `dashboard_bootstrap.py`:

```python
"""Create (or reuse), update and publish the sample dashboard as the app service principal."""

import json
from pathlib import Path

FOLDER = "aibi-embedding-demo"


class BootstrapError(RuntimeError):
    pass


def _canonical(serialized):
    d = json.loads(serialized) if isinstance(serialized, str) else serialized
    return json.dumps({"datasets": d.get("datasets", []), "pages": d.get("pages", [])}, sort_keys=True)


def probe_samples_access(ws, warehouse_id):
    body = ws.request(
        "POST",
        "/api/2.0/sql/statements",
        "samples access probe",
        json={"warehouse_id": warehouse_id, "statement": "SELECT r_name FROM samples.tpch.region LIMIT 1", "wait_timeout": "50s"},
    ).json()
    status = body.get("status", {})
    # PENDING / RUNNING just means the warehouse is still starting.
    if status.get("state") == "FAILED":
        message = status.get("error", {}).get("message", "unknown error")
        raise BootstrapError(
            f"The app service principal cannot read samples.tpch ({message}). "
            "GRANT it USE CATALOG on samples and USE SCHEMA, SELECT on samples.tpch."
        )


def ensure_dashboard(ws, warehouse_id, display_name, definition_file):
    if not warehouse_id:
        raise BootstrapError(
            "No SQL warehouse attached. Add an app resource of type SQL warehouse with key "
            "`sql-warehouse` and permission CAN USE, then redeploy."
        )
    serialized = Path(definition_file).read_text()
    parent = f"/Users/{ws.client_id}/{FOLDER}"
    ws.request("POST", "/api/2.0/workspace/mkdirs", "create folder", json={"path": parent})
    found = ws.request(
        "GET", "/api/2.0/workspace/get-status", "find dashboard",
        params={"path": f"{parent}/{display_name}.lvdash.json"}, allow_404=True,
    )
    if found is None:
        created = ws.request(
            "POST", "/api/2.0/lakeview/dashboards", "create dashboard",
            json={"display_name": display_name, "parent_path": parent, "warehouse_id": warehouse_id, "serialized_dashboard": serialized},
        ).json()
        dashboard_id = created["dashboard_id"]
    else:
        dashboard_id = found.json()["resource_id"]
        current = ws.request("GET", f"/api/2.0/lakeview/dashboards/{dashboard_id}", "read dashboard").json()
        changed = _canonical(current.get("serialized_dashboard", "{}")) != _canonical(serialized)
        if changed or current.get("warehouse_id") != warehouse_id:
            ws.request(
                "PATCH", f"/api/2.0/lakeview/dashboards/{dashboard_id}", "update dashboard",
                json={"serialized_dashboard": serialized, "warehouse_id": warehouse_id},
            )
    # embed_credentials=True: queries run with the publisher's (this app SP's) credentials.
    ws.request(
        "POST", f"/api/2.0/lakeview/dashboards/{dashboard_id}/published", "publish dashboard",
        json={"embed_credentials": True, "warehouse_id": warehouse_id},
    )
    return dashboard_id
```

- [ ] **Step 4: Run** Expected: 6 passed.
- [ ] **Step 5: Commit** `git commit -am "feat(part1): idempotent dashboard bootstrap"` (add new files first).

---

### Task 4: Part 1 app, page and manifest

**Files:**
- Create: `01-external-embedding/app.py`, `01-external-embedding/static/index.html`, `01-external-embedding/app.yaml`, `01-external-embedding/requirements.txt`
- Test: `01-external-embedding/tests/test_app.py`

**Interfaces:**
- Consumes: Tasks 1 and 3.
- Produces: `app` (FastAPI), `state` (`.ws`, `.dashboard_id`, `.error`, `.preparing`), `bootstrap() -> None`; routes `GET /`, `GET /api/health`, `GET /api/embed-config`, `POST /api/bootstrap`.

- [ ] **Step 1: Failing tests** `tests/test_app.py`:

```python
import pytest
from fastapi.testclient import TestClient

import app as app_module
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
```

- [ ] **Step 2: Run** Expected: FAIL.

- [ ] **Step 3: Implement** `app.py`:

```python
"""Part 1: a Databricks App that embeds an AI/BI dashboard with external-embed tokens."""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from dashboard_bootstrap import BootstrapError, ensure_dashboard, probe_samples_access
from dbx_client import ApiError, Workspace
from embed_token import mint_embed_token, viewer_id

HERE = Path(__file__).parent
DISPLAY_NAME = os.environ.get("DASHBOARD_NAME", "Sample Sales - External Embedding")
DEFINITION = HERE / "dashboard" / "sample.lvdash.json"


class State:
    ws = None
    dashboard_id = None
    error = None
    preparing = True


state = State()


def bootstrap():
    state.preparing, state.error = True, None
    try:
        state.ws = state.ws or Workspace.from_env()
        warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
        if warehouse_id:
            probe_samples_access(state.ws, warehouse_id)
        state.dashboard_id = ensure_dashboard(state.ws, warehouse_id, DISPLAY_NAME, DEFINITION)
    except KeyError as e:
        state.error = f"Missing environment variable {e}. Run this inside Databricks Apps or set it locally."
    except (ApiError, BootstrapError) as e:
        state.error = str(e)
    finally:
        state.preparing = False


@asynccontextmanager
async def lifespan(_app):
    # Bootstrap in the background so the app answers health checks immediately.
    threading.Thread(target=bootstrap, daemon=True).start()
    yield


app = FastAPI(title="AI/BI external embedding", lifespan=lifespan)


def _status():
    if state.error:
        return "error"
    return "ok" if state.dashboard_id else "preparing"


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": _status(),
        "dashboard_id": state.dashboard_id,
        "dashboard_url": f"{state.ws.host}/dashboardsv3/{state.dashboard_id}/published" if state.ws and state.dashboard_id else None,
        "warehouse_id": os.environ.get("DATABRICKS_WAREHOUSE_ID", ""),
        "error": state.error,
    }


@app.post("/api/bootstrap")
def retry_bootstrap():
    bootstrap()
    return health()


@app.get("/api/embed-config")
def embed_config(request: Request):
    if state.error:
        return JSONResponse({"error": state.error}, status_code=503)
    if not state.dashboard_id:
        return JSONResponse({"error": "The dashboard is still being prepared. Retry in a few seconds."}, status_code=503)
    email = request.headers.get("x-forwarded-email", "")
    try:
        minted = mint_embed_token(state.ws, state.dashboard_id, viewer_id(email))
        workspace_id = state.ws.workspace_id()
    except ApiError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {
        "workspace_url": state.ws.host,
        "workspace_id": workspace_id,
        "dashboard_id": state.dashboard_id,
        "token": minted["token"],
        "expires_in": minted["expires_in"],
        "viewer": email,
    }
```

`static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI/BI external embedding</title>
  <style>
    body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; background: #f6f7f9; color: #1b1f24; }
    header { display: flex; justify-content: space-between; align-items: center; background: #1b3139; color: #fff; padding: 14px 20px; }
    header h1 { margin: 0; font-size: 17px; font-weight: 600; }
    .message { margin: 16px 20px; padding: 12px 14px; border-radius: 6px; background: #fff4e5; border: 1px solid #f0b86e; }
    .dashboard { height: calc(100vh - 150px); min-height: 520px; margin: 16px 20px; background: #fff; border: 1px solid #e3e6ea; border-radius: 8px; }
    .status { margin: 0 20px 20px; font-size: 12px; }
    .status pre { background: #fff; border: 1px solid #e3e6ea; padding: 10px; overflow: auto; }
  </style>
</head>
<body>
  <header><h1>AI/BI dashboard - external embedding</h1><span id="viewer"></span></header>
  <div id="message" class="message" hidden></div>
  <div id="dashboard" class="dashboard"></div>
  <details class="status"><summary>Status</summary><pre id="status">loading...</pre></details>
  <script type="module">
    import { DatabricksDashboard } from "https://cdn.jsdelivr.net/npm/@databricks/aibi-client@1.2.0/+esm";

    const show = (text) => { const m = document.getElementById("message"); m.textContent = text; m.hidden = false; };

    async function fetchConfig() {
      const r = await fetch("/api/embed-config");
      const body = await r.json();
      if (!r.ok) throw new Error(body.error || r.statusText);
      return body;
    }

    async function main() {
      const health = await (await fetch("/api/health")).json();
      document.getElementById("status").textContent = JSON.stringify({ ...health, app_domain: location.host }, null, 2);
      let config;
      try { config = await fetchConfig(); } catch (e) { show(e.message); return; }
      document.getElementById("viewer").textContent = config.viewer || "";
      const dashboard = new DatabricksDashboard({
        instanceUrl: config.workspace_url,
        workspaceId: config.workspace_id,
        dashboardId: config.dashboard_id,
        token: config.token,
        container: document.getElementById("dashboard"),
        getNewToken: async () => (await fetchConfig()).token,
      });
      try {
        await dashboard.initialize();
      } catch (e) {
        show(`Embedding failed: ${e.message}. If the frame stays blank, ask a workspace admin to add ${location.host} to the approved embedding domains.`);
      }
    }
    main();
  </script>
</body>
</html>
```

`app.yaml`:

```yaml
command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql-warehouse
```

`requirements.txt`:
```
fastapi>=0.110
uvicorn[standard]>=0.29
requests>=2.31
```

- [ ] **Step 4: Run** `../.venv/bin/pytest -q` Expected: all Part 1 tests pass (22).
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(part1): app, embed page and manifest"`

---

### Task 5: Part 2 scope resolution

**Files:**
- Create: `02-group-based-rls/dbx_client.py`, `02-group-based-rls/embed_token.py`, `02-group-based-rls/dashboard_bootstrap.py` (byte-identical copies from Part 1), `02-group-based-rls/rls.py`, `02-group-based-rls/regions.json`, `02-group-based-rls/tests/conftest.py` (copy)
- Test: `02-group-based-rls/tests/test_rls.py`

**Interfaces:**
- Consumes: `Workspace.request(..., token=...)`, `ApiError`.
- Produces: `NO_ACCESS = "__no_access__"`; `load_config(path, env=os.environ) -> {"prefix": str, "admin_groups": set[str], "group_map": dict[str, str]}`; `resolve_scope(groups, cfg) -> str`; `get_viewer_groups(ws, user_token) -> list[str]`; `resolve_external_value(ws, headers, cfg) -> (str, {"groups": list, "scope": str, "reason": str})`.

- [ ] **Step 1: Copy shared modules**: `cp 01-external-embedding/{dbx_client,embed_token,dashboard_bootstrap}.py 02-group-based-rls/ && mkdir -p 02-group-based-rls/tests && cp 01-external-embedding/tests/conftest.py 02-group-based-rls/tests/`

- [ ] **Step 2: `regions.json`**:

```json
{
  "_README": "Group-to-region config. A group named <prefix><name> grants region <name> (hyphens become spaces, uppercased): region-middle-east -> MIDDLE EAST. Members of an admin group see all rows. 'groups' maps non-convention group names (lowercase) to a region. Env vars REGION_GROUP_PREFIX, ADMIN_GROUPS (comma list) and GROUP_REGION_MAP (group:REGION,...) override this file.",
  "prefix": "region-",
  "adminGroups": ["region-admin"],
  "groups": {}
}
```

- [ ] **Step 3: Failing tests** `tests/test_rls.py`:

```python
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
```

- [ ] **Step 4: Run** `cd 02-group-based-rls && ../.venv/bin/pytest -q tests/test_rls.py` Expected: FAIL.

- [ ] **Step 5: Implement** `rls.py`:

```python
"""Resolve the per-viewer RLS scope (external_value) from the viewer's Databricks groups.

The value surfaces in dashboard SQL as __aibi_external_value:
  ""              -> all rows (admin)
  "ASIA,EUROPE"   -> those regions
  "__no_access__" -> zero rows (fail closed)
It is decided here from the authenticated viewer, never from client input.
"""

import json
import os
from pathlib import Path

from dbx_client import ApiError

NO_ACCESS = "__no_access__"


def _parse_map(text):
    pairs = {}
    for item in (text or "").split(","):
        group, sep, region = item.rpartition(":")
        if sep and group.strip() and region.strip():
            pairs[group.strip().lower()] = region.strip().upper()
    return pairs


def load_config(path, env=os.environ):
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raw = {}
    admin = env.get("ADMIN_GROUPS")
    admin_groups = admin.split(",") if admin else raw.get("adminGroups", ["region-admin"])
    group_map = {k.lower(): v.upper() for k, v in raw.get("groups", {}).items()}
    group_map.update(_parse_map(env.get("GROUP_REGION_MAP", "")))
    return {
        "prefix": env.get("REGION_GROUP_PREFIX", raw.get("prefix", "region-")).lower(),
        "admin_groups": {g.strip().lower() for g in admin_groups if g.strip()},
        "group_map": group_map,
    }


def _region(suffix):
    return suffix.replace("-", " ").replace("_", " ").strip().upper()


def resolve_scope(groups, cfg):
    names = [g.strip().lower() for g in groups if g and g.strip()]
    if any(n in cfg["admin_groups"] for n in names):
        return ""
    regions = set()
    for name in names:
        if name in cfg["group_map"]:
            regions.add(cfg["group_map"][name])
        elif cfg["prefix"] and name.startswith(cfg["prefix"]) and name != cfg["prefix"]:
            regions.add(_region(name[len(cfg["prefix"]):]))
    return ",".join(sorted(regions)) if regions else NO_ACCESS


def get_viewer_groups(ws, user_token):
    # /Me with the viewer's own forwarded token: true identity, no admin rights needed.
    me = ws.request(
        "GET", "/api/2.0/preview/scim/v2/Me", "viewer groups",
        token=user_token, params={"attributes": "userName,groups"},
    ).json()
    return [g["display"] for g in me.get("groups", []) if g.get("display")]


def resolve_external_value(ws, headers, cfg):
    user_token = headers.get("x-forwarded-access-token", "")
    if not user_token:
        reason = "User authorization is not enabled for this app, so the viewer's groups cannot be read."
        return NO_ACCESS, {"groups": [], "scope": NO_ACCESS, "reason": reason}
    try:
        groups = get_viewer_groups(ws, user_token)
    except ApiError as e:
        return NO_ACCESS, {"groups": [], "scope": NO_ACCESS, "reason": f"Could not read the viewer's groups: {e}"}
    scope = resolve_scope(groups, cfg)
    reason = "" if scope != NO_ACCESS else "You are not in any region group."
    return scope, {"groups": groups, "scope": scope, "reason": reason}
```

- [ ] **Step 6: Run** Expected: 11 passed.
- [ ] **Step 7: Commit** `git add -A && git commit -m "feat(part2): group-based scope resolution"`

---

### Task 6: Part 2 app, page and manifest

**Files:**
- Create: `02-group-based-rls/app.py`, `02-group-based-rls/static/index.html`, `02-group-based-rls/app.yaml`, `02-group-based-rls/requirements.txt` (copy of Part 1's)
- Test: `02-group-based-rls/tests/test_app.py`

**Interfaces:**
- Consumes: Task 5 `resolve_external_value`, `load_config`; Part 1 module copies.
- Produces: same routes as Part 1; `/api/embed-config` adds `"scope": {"groups", "scope", "reason"}`.

- [ ] **Step 1: Failing tests** `tests/test_app.py`:

```python
import pytest
from fastapi.testclient import TestClient

import app as app_module


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
```

- [ ] **Step 2: Run** Expected: FAIL.

- [ ] **Step 3: Implement** `app.py`: identical to Part 1 `app.py` with these differences (write the full file):
  - Docstring: `"""Part 2: external embedding plus dashboard filtering based on the viewer's groups."""`
  - Add `from rls import load_config, resolve_external_value` and `RLS_CONFIG = load_config(HERE / "regions.json")`.
  - `DISPLAY_NAME` default `"Sample Sales - Group-Based RLS"`; `DEFINITION = HERE / "dashboard" / "sample_rls.lvdash.json"`; FastAPI title `"AI/BI group-based RLS"`.
  - In `embed_config`, before minting:

```python
    external_value, scope_info = resolve_external_value(state.ws, request.headers, RLS_CONFIG)
    try:
        minted = mint_embed_token(state.ws, state.dashboard_id, viewer_id(email), external_value)
        workspace_id = state.ws.workspace_id()
    except ApiError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
```
  and add `"scope": scope_info` to the returned dict.

`static/index.html`: Part 1's page with title `AI/BI dashboard - group-based filtering`, this element after `#message`:

```html
  <div id="scope" class="scope"></div>
```

this CSS:

```css
    .scope { margin: 16px 20px 0; padding: 10px 14px; border-radius: 6px; background: #eef6ff; border: 1px solid #9cc3ef; font-size: 13px; }
    .scope.denied { background: #fdecea; border-color: #f1a9a0; }
```

and, after `config` is fetched in `main()`:

```js
      const s = config.scope || {};
      const box = document.getElementById("scope");
      const scopeLabel = s.scope === "" ? "ALL regions (admin)" : s.scope === "__no_access__" ? "no access" : s.scope;
      box.textContent = `Your groups: ${(s.groups || []).join(", ") || "none"}  |  Dashboard scope: ${scopeLabel}`
        + (s.reason ? `  |  ${s.reason} Ask a workspace admin to add you to a region-<name> group (for example region-europe) or region-admin; see the README.` : "");
      if (s.scope === "__no_access__") box.classList.add("denied");
```

`app.yaml`: same as Part 1.

- [ ] **Step 4: Run** `../.venv/bin/pytest -q` Expected: all Part 2 tests pass (14).
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(part2): app with group-based filtering banner"`

---

### Task 7: Documentation

**Files:**
- Create: `README.md` (replace stub), `docs/01-external-embedding.md`, `docs/02-group-based-rls.md`, `docs/context-based-ingress.md`

- [ ] **Step 1: README** sections: what this is (two parts, links to docs); what "external users" means (spec section 2 text); prerequisites (Apps enabled, a SQL warehouse, admin for embedding domains); Quick start Part 1 (UI steps and CLI from spec 4.1 with real repo URL `https://github.com/Amitkpdatabricks/aibi-embedding-on-databricks-apps`); Quick start Part 2 (same with path `02-group-based-rls`, plus "Configure > User authorization: enable it (default scopes include `iam.current-user:read`)" and the group snippet below); admin steps; troubleshooting table (spec section 7); local tests (`uv venv -p 3.11 .venv && uv pip install -p .venv -r requirements-dev.txt`, then `pytest` in each folder); license.

Group snippet (workspace-level CLI; use `databricks account groups` for account-level):

```bash
for r in africa america asia europe middle-east admin; do
  databricks groups create --display-name "region-$r"
done
# add a user (ids from: databricks users list --filter 'userName eq "you@company.com"'; databricks groups list --filter 'displayName eq "region-europe"')
databricks groups patch <group-id> --json '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"members","value":[{"value":"<user-id>"}]}]}'
```

- [ ] **Step 2: docs/01** - how external embedding works on Apps: architecture, token flow, bootstrap behaviour, security notes; adapted from the end-to-end guide (sections 3-8). **docs/02** - scope resolution, SQL predicate, fail-closed rules, nested-group caveat, UC row filter backstop example:

```sql
CREATE FUNCTION main.security.region_filter(r_name STRING)
RETURN is_account_group_member('region-admin')
    OR is_account_group_member(concat('region-', lower(replace(r_name, ' ', '-'))));
```

**docs/context-based-ingress.md** - spec section 6.3 and the end-to-end guide section 11 (minimal contexts Apps runtime + `dashboard` + `settings`, the `settings` trap, dry-run then enforce, `system.access.inbound_network`).

- [ ] **Step 3: Check** `grep -rn "—" README.md docs/` Expected: no output.
- [ ] **Step 4: Commit** `git add -A && git commit -m "docs: README and guides"`

---

### Task 8: Publish to GitHub

- [ ] **Step 1:** Run all tests in both folders; all pass.
- [ ] **Step 2:** Print the Isaac Review tip (once), then `gh repo create Amitkpdatabricks/aibi-embedding-on-databricks-apps --public --source . --push --description "Deploy AI/BI dashboard external embedding and group-based RLS on Databricks Apps from Git"`
- [ ] **Step 3:** `gh repo view --web` URL recorded in the handoff.

---

### Task 9: End-to-end verification in a sandbox workspace (needs user sign-in)

- [ ] **Step 1:** Ask the user which workspace and confirm before creating apps. Default: `amit-apps-dev1` (azure-sandbox-field-eng).
- [ ] **Step 2:** `databricks apps create aibi-embed-p1 --git-url https://github.com/Amitkpdatabricks/aibi-embedding-on-databricks-apps --git-provider gitHub`; add the `sql-warehouse` resource; `databricks apps deploy aibi-embed-p1 --git-branch main --git-source-code-path 01-external-embedding`.
- [ ] **Step 3:** Verify `/api/health` is `ok`, the dashboard renders, and resolve spec section 10 items (samples access, scopes, CDN, embedding-domain setting). Fix code or docs for any finding and push.
- [ ] **Step 4:** Repeat for Part 2 with user authorization enabled; check as no-group, `region-europe`, `region-europe` + `region-asia`, `region-admin`.
