# AI/BI Dashboard Embedding on Databricks Apps - Design

Date: 2026-10-12
Status: Draft for review

## 1. Goal

A public GitHub repo that any Databricks user can point a **Git-backed Databricks App** at and, with minimal effort, see:

- **Part 1 - How to make Databricks Apps for external users with an externally embedded AI/BI dashboard.**
- **Part 2 - How to enable dashboard filtering based on the user's group (row-level security).**

Success criteria:

1. In any workspace, a user creates an app from Git (UI or CLI), sets the source code path to `01-external-embedding` (or `02-group-based-rls`), attaches one SQL warehouse, and deploys. No code edits.
2. On first start the app creates and publishes its own sample dashboard on `samples.tpch` and embeds it with a scoped external-embed token.
3. Part 2 shows each viewer only the rows for the region(s) their Databricks groups grant. Viewers without a region group see zero rows and a banner explaining how to get access.
4. The only manual steps are those that need admin rights, and each is a copy-paste snippet in the README.

## 2. What "external users" means here

A Databricks App URL always triggers Databricks OAuth (backed by the workspace IdP, e.g. Entra ID SSO), so **viewers must be users provisioned in the Databricks account** (for example, Entra B2B guests). External embedding removes the need for viewers to have:

- workspace UI access,
- CAN VIEW / CAN RUN on the dashboard,
- any Unity Catalog grants on the data.

All queries run as the app's service principal. A fully login-less viewer requires hosting the app outside Databricks and is out of scope (linked as further reading).

## 3. Repository layout

```
aibi-embedding-on-databricks-apps/
  README.md                       Overview, quick start for both parts, admin steps
  01-external-embedding/          Part 1 - self-contained app (Git source code path)
    app.yaml
    requirements.txt
    app.py                        FastAPI: routes + startup bootstrap
    embed_token.py                3-step scoped token exchange
    dashboard_bootstrap.py        Create / find / publish the sample dashboard
    dashboard/sample.lvdash.json  Dashboard definition (samples.tpch)
    static/index.html             Page + @databricks/aibi-client embed (CDN, no build)
    tests/
  02-group-based-rls/             Part 2 - self-contained app
    (same files as Part 1, plus)
    rls.py                        Viewer groups -> external_value
    regions.json                  Group-to-region config
    dashboard/sample_rls.lvdash.json  Same dashboard, datasets filtered on __aibi_external_value
    tests/
  docs/
    01-external-embedding.md      Part 1 guide (concepts + step-by-step)
    02-group-based-rls.md         Part 2 guide
    context-based-ingress.md      Optional: public app, private workspace
    superpowers/specs/            This spec
  LICENSE
```

Each app folder is fully self-contained because a Git-backed app cannot read files outside its source code path. Part 2 duplicates Part 1's small modules (`embed_token.py`, `dashboard_bootstrap.py`) rather than sharing them.

## 4. Part 1 - External embedding

### 4.1 User experience

1. Workspace admin (once): add `*.databricksapps.com` (or the app URL) to the workspace's approved embedding domains.
2. User: **Apps > Create app > Custom app > Configure Git**: repo URL, provider GitHub, branch `main`.
3. User: add resource **SQL warehouse**, permission **CAN USE**, key `sql-warehouse`.
4. User: **Deploy > From Git**, source code path `01-external-embedding`.
5. Open the app URL: the dashboard renders.

Equivalent CLI:

```bash
databricks apps create aibi-embed-demo --git-url https://github.com/Amitkpdatabricks/<repo> --git-provider gitHub
databricks apps deploy aibi-embed-demo --git-branch main --git-source-code-path 01-external-embedding
```

(The warehouse resource is added in the UI or with `databricks apps update`.)

### 4.2 Components

| Unit | Purpose | Depends on |
|---|---|---|
| `app.yaml` | Runs uvicorn; maps `DATABRICKS_WAREHOUSE_ID` from the `sql-warehouse` resource (`valueFrom`) | Apps runtime |
| `dashboard_bootstrap.py` | Idempotently ensures the dashboard exists and is published; returns its ID | Lakeview REST API, app SP token |
| `embed_token.py` | Mints a scoped embed token for one viewer and dashboard | `/oidc/v1/token`, `/published/tokeninfo` |
| `app.py` | `GET /` page, `GET /api/embed-config`, `GET /api/health`; runs bootstrap at startup | the two modules above |
| `static/index.html` | Loads `@databricks/aibi-client` from a CDN, renders the dashboard, refreshes the token via `getNewToken` | `/api/embed-config` |

### 4.3 Dashboard bootstrap (idempotent)

At startup, as the app service principal (credentials injected by the Apps runtime):

1. Target path: `/Workspace/Users/<app-sp-application-id>/aibi-embedding-demo/Sample Sales - External Embedding.lvdash.json`.
2. Look up the path (`workspace get-status`). If found, reuse its dashboard ID; otherwise `POST /api/2.0/lakeview/dashboards` with `display_name`, `parent_path`, `warehouse_id` and `serialized_dashboard` from `dashboard/sample.lvdash.json`.
3. If found, `GET` the dashboard and compare its `serialized_dashboard` (normalised JSON) with the repo file. If they differ, `PATCH` it so redeploys pick up changes.
4. `POST /api/2.0/lakeview/dashboards/{id}/published` with `embed_credentials: true` and the warehouse ID.
5. Cache the ID in memory. If bootstrap fails, the app still starts and `/api/health` plus the page show the error and the fix.

Because the app SP owns the dashboard, it already has the permissions needed to publish and mint tokens. The SP needs `USE CATALOG` / `USE SCHEMA` / `SELECT` on `samples.tpch`; bootstrap runs a one-row probe query and reports a clear error if access is missing.

### 4.4 Sample dashboard (`samples.tpch`)

One page with a global region filter:

- KPI tiles: total revenue, order count, customer count.
- Revenue by region (bar), revenue by month (line), top 10 nations (bar), order status mix (pie).

Base dataset: `orders` join `customer` join `nation` join `region`, exposing `region` (`AFRICA`, `AMERICA`, `ASIA`, `EUROPE`, `MIDDLE EAST`), `nation`, `order_date`, `order_status`, `revenue = o_totalprice`.

### 4.5 Embed token flow

Per request to `/api/embed-config`:

1. `external_viewer_id` = first 32 hex characters of `sha256(lower(x-forwarded-email))` (stable, non-PII).
2. `external_value` = empty in Part 1.
3. Three calls: SP token (`client_credentials`, `all-apis`, cached until near expiry) -> `tokeninfo` with viewer context -> scoped token with `authorization_details`.
4. Returns `{workspace_url, workspace_id, dashboard_id, token}`. The SP secret never reaches the browser.

### 4.6 Page

- Header with app name and the signed-in user.
- Embedded dashboard.
- Collapsible **Status** panel: dashboard ID and link, warehouse ID, last token expiry, and setup hints (for example, "Blank frame? Add this app's domain to the approved embedding domains" with the exact domain shown).

## 5. Part 2 - Group-based filtering (RLS)

### 5.1 User experience

Same as Part 1 with source code path `02-group-based-rls`, plus an admin creates groups:

| Group | Grants |
|---|---|
| `region-africa`, `region-america`, `region-asia`, `region-europe`, `region-middle-east` | That region |
| `region-admin` | All rows |

The README provides a copy-paste CLI loop that creates these groups and adds a user. Groups should be account-level so the same names work with Unity Catalog row filters.

### 5.2 Scope resolution (`rls.py`)

Per token request, never cached:

1. Viewer identity from `x-forwarded-email` (set by the Apps runtime; cannot be forged).
2. Groups from `GET /api/2.0/preview/scim/v2/Me?attributes=userName,groups` with `x-forwarded-access-token` (default `iam.current-user:read` scope). No admin rights needed.
3. Map groups with `regions.json`: prefix `region-`, admin groups `["region-admin"]`, optional explicit map for non-convention names. Environment variables `REGION_GROUP_PREFIX`, `ADMIN_GROUPS`, `GROUP_REGION_MAP` override the file.
4. Result:
   - admin -> `""` (all rows)
   - one or more regions -> sorted, comma-separated codes, for example `ASIA,EUROPE`
   - none -> `__no_access__` (zero rows, fail closed)
5. If the forwarded token is missing or `/Me` fails, fail closed with `__no_access__` and show the reason.

No client-supplied scope is ever honoured.

### 5.3 Dashboard SQL

Every dataset in `sample_rls.lvdash.json` applies the predicate before aggregation:

```sql
WHERE nullif(__aibi_external_value, '') IS NULL
   OR array_contains(split(__aibi_external_value, ','), upper(r_name))
```

The Part 2 dashboard is created at a different path from Part 1, so both apps can run in the same workspace.

### 5.4 Page additions

A banner shows the viewer's groups, the resolved scope, and, when the scope is `__no_access__`, the exact command an admin runs to add the viewer to a region group. The banner is informational only and cannot change the scope.

### 5.5 Security notes (documented)

- The empty value means all rows, so the server must always send a non-empty value to non-admins. The code enforces this, and the tests cover it.
- SCIM `/Me` returns direct memberships only. Nested groups are not expanded, so they fail closed.
- Dashboard RLS does not protect direct table access. The guide shows the matching Unity Catalog row filter with `is_account_group_member()` as a backstop.

## 6. Admin-only steps (cannot be automated by the app)

1. Approved embedding domains (Part 1 and 2).
2. Region groups and memberships (Part 2).
3. Optional context-based ingress policy for a private workspace with a public app (`docs/context-based-ingress.md`): allow Apps runtime, workspace API `dashboard` and `settings`.

## 7. Error handling

| Failure | Behaviour |
|---|---|
| No warehouse resource | Health and page show "Attach a SQL warehouse resource with key `sql-warehouse`" |
| SP cannot read `samples.tpch` | Probe fails; page shows the GRANT statements needed |
| Dashboard create or publish fails | App starts; page shows the API error; redeploy to retry (a viewer-callable retry endpoint was dropped in review: any signed-in viewer could re-run bootstrap for everyone) |
| Token mint fails | 502 from `/api/embed-config` with the failing step number; frontend shows it |
| Embedding domain not approved | Frame stays blank; the status panel shows the domain to approve |
| Part 2: `/Me` fails or no groups | Fail closed (`__no_access__`), banner explains why |

## 8. Testing

- Unit tests (pytest, HTTP calls mocked): token exchange request shapes and caching, viewer ID hashing, bootstrap idempotency (exists, missing, changed), group-to-scope mapping including admin, multi-region, none, legacy map, env overrides and the never-empty rule.
- Definition tests: both `.lvdash.json` files parse; every Part 2 dataset contains the RLS predicate.
- Manual end-to-end in a sandbox workspace: create both apps from Git, verify render, then test as users in no group, `region-europe`, `region-europe` plus `region-asia`, and `region-admin`.

## 9. Out of scope

- Login-less external viewers (app hosted outside Databricks).
- Genie in the embedded experience.
- Terraform or DAB packaging (possible follow-up).
- Automating the admin steps.

## 10. Items to verify during implementation

1. The app SP's default access to `samples.tpch` in a fresh workspace.
2. Whether `app.yaml` needs the `dashboards` and `settings` scopes for the token exchange, as in an earlier customer implementation.
3. The current `@databricks/aibi-client` CDN URL and version.
4. The exact settings API name for approved embedding domains, for the README admin snippet.
