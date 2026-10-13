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
