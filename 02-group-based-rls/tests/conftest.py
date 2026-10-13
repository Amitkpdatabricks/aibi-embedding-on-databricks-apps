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
