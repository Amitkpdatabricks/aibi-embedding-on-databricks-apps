"""AI/BI external-embed token exchange.

Docs: https://docs.databricks.com/aws/en/dashboards/embedding/external-embed

1. POST /oidc/v1/token (client_credentials, all-apis)                -> SP token
2. GET  /api/2.0/lakeview/dashboards/{id}/published/tokeninfo        -> authorization_details
3. POST /oidc/v1/token (client_credentials + authorization_details)  -> scoped embed token
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
