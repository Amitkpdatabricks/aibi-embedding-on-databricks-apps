# Part 1 - External Embedding on Databricks Apps

This guide explains what [`01-external-embedding`](../01-external-embedding) does and why. To deploy it, follow the [README quick start](../README.md#part-1-quick-start).

## The idea

Basic iframe embedding renders a dashboard **as the viewer**, so every viewer needs workspace access and permission on the dashboard and data. **External embedding** turns this around:

- The app's **service principal** owns the dashboard and the data access.
- For each viewer, the app backend mints a **short-lived token scoped to one dashboard** and labelled with a viewer id for audit.
- The browser renders the dashboard with that token using the `@databricks/aibi-client` SDK.

Viewers still sign in to the Databricks App (see [What "external users" means here](../README.md#what-external-users-means-here)), but they need no other access.

## Architecture

```
Viewer's browser
   |  1. opens https://<app>.databricksapps.com  (Databricks sign-in)
   v
Databricks App (FastAPI, one container)
   |  2. on startup: create + publish the sample dashboard as the app service principal
   |  3. per page load: GET /api/embed-config -> 3-step token exchange
   v
Browser: DatabricksDashboard(token) renders the dashboard
   |  4. dashboard queries run as the app service principal
   v
SQL warehouse  ->  samples.tpch
```

## What happens on first start

`dashboard_bootstrap.py`, running as the app service principal:

1. Checks that a SQL warehouse is attached (resource key `sql-warehouse`) and that the service principal can read `samples.tpch`, with a one-row probe query.
2. Looks for `/Users/<app-sp-id>/aibi-embedding-demo/Sample Sales - External Embedding.lvdash.json`.
3. Creates the dashboard from `dashboard/sample.lvdash.json` if it is missing. If it exists and the repo version changed, it updates it. Redeploys never create duplicates.
4. Publishes it with embedded credentials, so queries run as the service principal.

Because the service principal created the dashboard, it already has every permission the token exchange needs.

## The token exchange

`embed_token.py`, per page load:

| Step | Call | Purpose |
|---|---|---|
| 1 | `POST /oidc/v1/token` with `grant_type=client_credentials`, `scope=all-apis` | Service principal token (cached until shortly before expiry) |
| 2 | `GET /api/2.0/lakeview/dashboards/{id}/published/tokeninfo?external_viewer_id=...` | Returns the `authorization_details` for this dashboard and viewer |
| 3 | `POST /oidc/v1/token` with `grant_type=client_credentials` and those `authorization_details` | Returns the scoped embed token (about 1 hour) |

`external_viewer_id` is a SHA-256 hash of the signed-in email (taken from the `x-forwarded-email` header the Apps runtime sets). The docs require this value to be unique per viewer and free of PII. It appears in the audit logs.

The browser receives only the scoped token, never the service principal secret. The SDK calls `getNewToken` before the token expires, and the backend simply mints a fresh one.

## Security notes

- The service principal's grants are the security boundary. In real use, grant it `SELECT` only on the views a dashboard needs.
- Downloads from embedded dashboards are on by default. A workspace admin can disable them.
- External embedding is limited to 20 dashboard loads per second per workspace. `external_viewer_id` plus `external_value` must stay under 1 KB.
- Ask Genie is not available in externally embedded dashboards.

## Files

| File | Purpose |
|---|---|
| `app.yaml` | Starts uvicorn and maps the `sql-warehouse` resource to `DATABRICKS_WAREHOUSE_ID` |
| `app.py` | Routes: `/`, `/api/embed-config`, `/api/health`; runs the bootstrap once at startup (redeploy to rerun it) |
| `dbx_client.py` | Small REST client authenticated as the app service principal |
| `embed_token.py` | Viewer id and the 3-step token exchange |
| `dashboard_bootstrap.py` | Create, update and publish the sample dashboard |
| `dashboard/sample.lvdash.json` | The dashboard definition on `samples.tpch` |
| `static/index.html` | The page and the SDK embed |

## References

- [Embed dashboards for external users](https://docs.databricks.com/aws/en/dashboards/embedding/external-embed)
- [How to embed AI/BI dashboards in customer-facing applications](https://www.databricks.com/blog/how-embed-databricks-aibi-dashboards-customer-facing-applications)
- [Deploy a Databricks app from Git](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
