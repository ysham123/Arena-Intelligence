"""Small standard-library client for Arena's local synthetic telemetry API.

No model service or simulation engine is required by this client.
"""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BODY_BYTES = 256 * 1024
MAX_EVENTS = 100


class AdapterError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a local bearer token to a redirected destination.
        return None


def local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Arena URL must be an HTTP(S) loopback origin, for example http://127.0.0.1:8767"
        )
    # Accessing .port also rejects malformed or out-of-range ports.
    _ = parsed.port
    return value.rstrip("/")


class ArenaAdapter:
    """Create a connection, then publish monotonic, retryable simulation events."""

    def __init__(
        self, base_url: str = "http://127.0.0.1:8767", token: str | None = None, timeout: float = 10
    ):
        self.base_url = local_base_url(base_url)
        if token and any(ord(c) <= 32 or ord(c) >= 127 for c in token):
            raise ValueError("integration tokens must use printable ASCII without whitespace")
        self.token = token
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.timeout = timeout
        self.opener = build_opener(NoRedirects())

    def _post(self, path: str, payload: dict, *, authenticated: bool = False) -> dict:
        encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError("telemetry batch exceeds the 256 KiB API limit")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if authenticated:
            if not self.token:
                raise AdapterError("create a connection or provide an integration token first")
            headers["Authorization"] = "Bearer " + self.token
        request = Request(self.base_url + path, data=encoded, headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
        except HTTPError as error:
            raise AdapterError(f"Arena rejected the request (HTTP {error.code})") from None
        except (URLError, TimeoutError, OSError):
            raise AdapterError(
                "Arena is unavailable; start the local workspace and check its URL"
            ) from None
        if len(raw) > MAX_BODY_BYTES:
            raise AdapterError("Arena returned an oversized response")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise AdapterError("Arena returned invalid JSON") from None
        if not isinstance(value, dict):
            raise AdapterError("Arena returned an unexpected response")
        return value

    def connect(self, name: str, description: str = "Synthetic simulation adapter") -> dict:
        result = self._post(
            "/api/connections",
            {"name": name, "adapter": "simulation-v1", "description": description},
        )
        token = result.get("integration_token")
        if not isinstance(token, str) or not token:
            raise AdapterError("connection did not return a one-time integration token")
        self.token = token
        connection = result.get("connection")
        if not isinstance(connection, dict) or not isinstance(connection.get("id"), str):
            raise AdapterError("connection returned an unexpected identity")
        return connection

    def publish(
        self, external_id: str, name: str, events: list[dict], *, status: str = "running"
    ) -> dict:
        if not 1 <= len(events) <= MAX_EVENTS:
            raise ValueError("publish between 1 and 100 events per batch")
        if status not in {"running", "completed", "stopped"}:
            raise ValueError("session status must be running, completed, or stopped")
        return self._post(
            "/api/ingest",
            {
                "schema_version": 1,
                "scope": "synthetic_simulation",
                "session": {"external_id": external_id, "name": name, "status": status},
                "events": events,
            },
            authenticated=True,
        )
