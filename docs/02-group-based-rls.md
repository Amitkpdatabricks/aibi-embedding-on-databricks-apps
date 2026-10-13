# Part 2 - Dashboard Filtering Based on the User's Groups

[`02-group-based-rls`](../02-group-based-rls) is Part 1 plus row-level security: each viewer sees only the regions their Databricks groups grant. To deploy it, follow the [README quick start](../README.md#part-2-quick-start).

## Why groups, and why in the app

In external embedding every query runs as the app service principal. Inside the dashboard, `current_user()` and `is_account_group_member()` therefore describe the **service principal, not the viewer**. The viewer's scope has to travel with the embed token instead:

1. The app decides the viewer's scope on the server.
2. It passes the scope as `external_value` when minting the token. The value is signed into the token and cannot be changed by the viewer.
3. Dashboard SQL reads it back as the read-only variable `__aibi_external_value`.

## How the scope is resolved

`rls.py`, on every token request (never cached, so group changes apply on the next page load):

1. **Who is the viewer?** The `x-forwarded-email` header, which the Apps runtime sets and the viewer cannot forge.
2. **Which groups?** `GET /api/2.0/preview/scim/v2/Me` with the viewer's own forwarded token (`x-forwarded-access-token`). This needs **user authorization** enabled on the app, and only the default `iam.current-user:read` scope. No admin rights are needed.
3. **Groups to regions**, using `regions.json`:
   - `region-<name>` grants that region. Hyphens become spaces and the name is uppercased, so `region-middle-east` becomes `MIDDLE EAST`.
   - Members of `region-admin` see all rows.
   - Group names that don't follow the convention can be mapped explicitly in `groups`.
4. **The value sent:**

| Viewer | `external_value` | Dashboard shows |
|---|---|---|
| `region-admin` (with or without other groups) | `""` | All rows |
| `region-europe` | `EUROPE` | Europe only |
| `region-europe` and `region-asia` | `ASIA,EUROPE` (sorted) | Both |
| No region group | `__no_access__` | No rows |
| User authorization off, or `/Me` fails | `__no_access__` | No rows, with the reason in the banner |

Nothing the browser sends can change the scope. Query parameters and request bodies are ignored.

Configuration can be overridden per app with environment variables in `app.yaml`: `REGION_GROUP_PREFIX`, `ADMIN_GROUPS` (comma list) and `GROUP_REGION_MAP` (`group:REGION,...`).

## The dashboard SQL

Every dataset in `dashboard/sample_rls.lvdash.json` applies the filter before any aggregation:

```sql
WHERE nullif(__aibi_external_value, '') IS NULL                             -- admin: all rows
   OR array_contains(split(__aibi_external_value, ','), upper(r.r_name))   -- 'EUROPE' or 'ASIA,EUROPE'
```

A plain `= __aibi_external_value` only matches a single region, so the membership form is what makes multi-region viewers work.

## Fail-closed rules

- An empty value means "all rows", so the app only ever sends it to admins. Everyone else gets either a region list or the `__no_access__` sentinel, which matches nothing.
- Any error reading the viewer's groups gives `__no_access__`, never all rows.
- The repo's tests check that every dataset in the Part 2 dashboard contains the predicate, so a dataset cannot ship without it.

## Caveats

- **Nested groups.** SCIM `/Me` returns direct memberships only. A user who is in a region group only through a parent group gets no access. Add users to region groups directly, or extend `get_viewer_groups()`.
- **Direct table access.** The dashboard filter protects the embedded dashboard only. If the same people can query the tables in a SQL editor or notebook, add a Unity Catalog row filter keyed to their real identity, using **account-level** groups:

```sql
CREATE FUNCTION main.security.region_filter(r_name STRING)
RETURN is_account_group_member('region-admin')
    OR is_account_group_member(concat('region-', lower(replace(r_name, ' ', '-'))));

ALTER TABLE main.sales.orders_by_region SET ROW FILTER main.security.region_filter ON (r_name);
```

## References

- [Beyond embedding: how to secure AI/BI dashboards for every viewer](https://www.databricks.com/blog/beyond-embedding-how-secure-aibi-dashboards-every-viewer)
- [Databricks Apps authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)
- [Row filters and column masks](https://docs.databricks.com/aws/en/tables/row-and-column-filters)
