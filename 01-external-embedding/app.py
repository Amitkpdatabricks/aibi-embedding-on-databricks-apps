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
_bootstrap_lock = threading.Lock()


def bootstrap():
    # One run at a time, so two runs can never both create the dashboard.
    if not _bootstrap_lock.acquire(blocking=False):
        return
    state.preparing, state.error = True, None
    try:
        try:
            state.ws = state.ws or Workspace.from_env()
        except KeyError as e:
            state.error = f"Missing environment variable {e}. Run this inside Databricks Apps or set it locally."
            return
        warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
        if warehouse_id:
            probe_samples_access(state.ws, warehouse_id)
        state.dashboard_id = ensure_dashboard(state.ws, warehouse_id, DISPLAY_NAME, DEFINITION)
    except (ApiError, BootstrapError) as e:
        state.error = str(e)
    except Exception as e:  # network errors or unexpected responses must not leave the app "preparing"
        state.error = f"Unexpected bootstrap error: {e!r}. Redeploy the app to retry."
    finally:
        state.preparing = False
        _bootstrap_lock.release()


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
