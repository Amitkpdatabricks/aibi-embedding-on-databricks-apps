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
    group_map = {k.lower(): v.strip().upper() for k, v in raw.get("groups", {}).items() if v.strip()}
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
        elif cfg["prefix"] and name.startswith(cfg["prefix"]):
            regions.add(_region(name[len(cfg["prefix"]):]))
    # An empty region would turn into "" (all rows), so it never counts as a grant.
    regions.discard("")
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
