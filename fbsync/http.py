"""A very small JSON over HTTP helper on top of urllib.

The transport is a plain function, so tests and the demo can swap it for a
fake one and nothing touches the network.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable

# transport(method, url, headers, body_bytes) -> (status, headers, body_bytes)
Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict, bytes]"]


class ApiError(Exception):
    """An API answered with an error. The message never includes headers,
    so tokens can't leak into logs."""

    def __init__(self, service: str, method: str, path: str, status: int, detail: str):
        self.status = status
        super().__init__(f"{service} {method} {path} failed with HTTP {status}: {detail}")


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float = 30):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class JsonClient:
    """Sends JSON, reads JSON, and retries 429 and 5xx a few times."""

    def __init__(self, service: str, base_url: str, headers: dict,
                 transport: Transport | None = None, max_retries: int = 4,
                 sleep: Callable[[float], None] = time.sleep):
        self.service = service
        self.base_url = base_url.rstrip("/")
        self._headers = headers  # holds the token, never printed
        self.transport = transport or urllib_transport
        self.max_retries = max_retries
        self.sleep = sleep

    def request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None):
        url = self.base_url + path
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self._headers)
        if data is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(self.max_retries + 1):
            status, resp_headers, raw = self.transport(method, url, headers, data)
            if status == 429 or status >= 500:
                if attempt < self.max_retries:
                    self.sleep(_retry_after(resp_headers, attempt))
                    continue
            if status >= 400:
                raise ApiError(self.service, method, path, status, _error_detail(raw))
            return json.loads(raw) if raw else None
        raise AssertionError("unreachable")


def _retry_after(headers: dict, attempt: int) -> float:
    for k, v in (headers or {}).items():
        if k.lower() == "retry-after":
            try:
                return min(float(v), 120.0)
            except ValueError:
                break
    return min(2 ** attempt, 30)


def _error_detail(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return raw[:200].decode(errors="replace") if raw else "no body"
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):  # Featurebase: {"error": {"type", "code", "message"}}
        return f"{err.get('code')}: {err.get('message')}"
    if isinstance(payload, dict) and "code" in payload:  # Notion: {"code", "message"}
        return f"{payload.get('code')}: {payload.get('message')}"
    return str(payload)[:200]
