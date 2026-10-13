# AI/BI Dashboard Embedding on Databricks Apps

Two small Databricks Apps that you deploy **straight from this Git repo** into any workspace:

| Part | Folder (Git source code path) | What it shows |
|---|---|---|
| 1 | [`01-external-embedding`](01-external-embedding) | How to make a Databricks App for external users with an **externally embedded AI/BI dashboard** |
| 2 | [`02-group-based-rls`](02-group-based-rls) | How to **filter the dashboard by the viewer's groups** (row-level security) |

Each app creates and publishes its own sample dashboard on `samples.tpch` the first time it starts, so there is nothing to import and no code to edit. You attach one SQL warehouse and deploy.

Guides:

- [Part 1 - External embedding on Databricks Apps](docs/01-external-embedding.md)
- [Part 2 - Dashboard filtering based on the user's groups](docs/02-group-based-rls.md)
- [Optional - Public app, private workspace with context-based ingress](docs/context-based-ingress.md)

## What "external users" means here

Opening a Databricks App URL always starts a Databricks sign-in (backed by your workspace identity provider, for example Entra ID SSO). Viewers must therefore be **users in your Databricks account**, for example B2B guests.

What external embedding removes is everything else. Viewers do **not** need:

- access to the workspace UI,
- any permission on the dashboard,
- any Unity Catalog grant on the data.

All queries run as the app's service principal, using a short-lived token that is scoped to one dashboard and one viewer. A completely login-less viewer needs the app to be hosted outside Databricks. That is out of scope here; see the [official reference implementation](https://github.com/databricks-solutions/aibi-dashboards-external-embedding).

## Prerequisites

- A workspace with Databricks Apps enabled.
- A SQL warehouse (serverless recommended) that you can grant **CAN USE** on.
- A workspace admin for the one-time steps below.

## One-time admin steps

1. **Approve the app domain for embedding.** In the workspace admin settings, open the **Security** tab, find the AI/BI dashboard embedding setting, allow embedding for approved domains, and add `*.databricksapps.com` (or your app's exact URL host).
2. **Part 2 only: create the region groups** and add testers (see [Part 2 quick start](#part-2-quick-start)).

## Part 1 quick start

UI:

1. **Compute > Apps > Create app > Create a custom app**, enter a name such as `aibi-embed-demo`, then **Next: Configure Git**.
2. Repository URL `https://github.com/Amitkpdatabricks/aibi-embedding-on-databricks-apps`, provider **GitHub**. The repo is public, so no Git credential is needed.
3. Under **App resources**, add **SQL warehouse**, permission **Can use**, resource key **`sql-warehouse`**.
4. Create the app, then **Deploy > From Git**: branch `main`, source code path **`01-external-embedding`**.
5. Open the app URL. On first start it creates the dashboard, so give it up to a minute.

CLI (Databricks CLI 1.14.0 or later):

```bash
databricks apps create aibi-embed-demo \
  --git-url https://github.com/Amitkpdatabricks/aibi-embedding-on-databricks-apps \
  --git-provider gitHub
# add the SQL warehouse resource (key: sql-warehouse, CAN USE) in the app's Edit page, then:
databricks apps deploy aibi-embed-demo --git-branch main --git-source-code-path 01-external-embedding
```

Expected result: the dashboard renders inside the app and everyone sees all regions. Expand **Status** at the bottom of the page for the dashboard ID, a link to it, and any setup error.

## Part 2 quick start

1. Follow the Part 1 steps with a new app name (for example `aibi-rls-demo`) and source code path **`02-group-based-rls`**.
2. Edit the app and enable **User authorization**. The default scopes include `iam.current-user:read`, which is all the app needs to read the viewer's groups. Redeploy.
3. A workspace admin creates the region groups and adds testers:

```bash
for r in africa america asia europe middle-east admin; do
  databricks groups create --display-name "region-$r"
done

# Look up the ids, then add a user to a group
databricks users list --filter 'userName eq "you@company.com"'
databricks groups list --filter 'displayName eq "region-europe"'
databricks groups patch <group-id> --json '{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [{"op": "add", "path": "members", "value": [{"value": "<user-id>"}]}]
}'
```

These are workspace-level groups, which is enough for the dashboard filter. If you also want Unity Catalog row filters (see the Part 2 guide), create them as account groups with `databricks account groups create` and assign them to the workspace.

| Group | Viewer sees |
|---|---|
| `region-africa`, `region-america`, `region-asia`, `region-europe`, `region-middle-east` | That region; several groups give several regions |
| `region-admin` | All rows |
| none of these | No rows, plus a banner explaining what to ask for |

The banner at the top of the page shows the viewer's groups and the resulting scope. Group changes apply on the next page load.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No SQL warehouse attached" | Add a SQL warehouse resource with key `sql-warehouse` and CAN USE, then redeploy |
| "cannot read samples.tpch" | Grant the app service principal `USE CATALOG` on `samples` and `USE SCHEMA`, `SELECT` on `samples.tpch` |
| "dashboard is still being prepared" | First start is creating the dashboard; reload after a few seconds |
| Dashboard frame stays blank | Approve the app domain for embedding (admin step 1); the Status panel shows the exact domain |
| Error mentioning "step 2" or "step 3" | Token exchange failed; check the dashboard is published (Status panel link) and redeploy to rerun the bootstrap |
| Part 2 says "User authorization is not enabled" | Enable user authorization on the app and redeploy |
| Part 2 shows no rows for a user in a parent group | Groups are read as direct memberships; add the user to the region group directly |

## Run the tests locally

```bash
uv venv -p 3.11 .venv && uv pip install -p .venv -r requirements-dev.txt
(cd 01-external-embedding && ../.venv/bin/pytest -q)
(cd 02-group-based-rls && ../.venv/bin/pytest -q)
```

## How it is built

- FastAPI backend; the browser renders the dashboard with [`@databricks/aibi-client`](https://www.npmjs.com/package/@databricks/aibi-client) loaded from a CDN, so there is no frontend build.
- The service principal credentials are injected by the Apps runtime and never reach the browser.
- Each app folder is self-contained, because a Git-backed app can only read files under its source code path. Part 2 therefore carries copies of Part 1's small modules.

## License

[Apache 2.0](LICENSE). This is a sample, not an official Databricks product; it is not covered by Databricks support.
