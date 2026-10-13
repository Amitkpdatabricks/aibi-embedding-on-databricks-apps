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
