"""Featurebase API v2: only the calls this tool needs.

Docs: https://help.featurebase.app/articles/2088519-featurebase-api
"""
from __future__ import annotations

from typing import Iterator

from .http import JsonClient, Transport

BASE_URL = "https://do.featurebase.app"
# Pinned so a new Featurebase version can't change field names under us.
API_VERSION = "2026-08-19.orbit"
PAGE_LIMIT = 100  # the documented maximum


class Featurebase:
    def __init__(self, api_key: str, transport: Transport | None = None, **kw):
        self.http = JsonClient(
            "Featurebase", BASE_URL,
            {"Authorization": f"Bearer {api_key}", "Featurebase-Version": API_VERSION,
             "Accept": "application/json"},
            transport=transport, **kw)

    def iter_posts(self, limit: int = PAGE_LIMIT) -> Iterator[dict]:
        """GET /v2/posts page by page until nextCursor is null."""
        cursor = None
        seen_cursors = set()
        while True:
            page = self.http.request("GET", "/v2/posts", params={"limit": limit, "cursor": cursor})
            yield from page.get("data") or []
            cursor = page.get("nextCursor")
            if not cursor:
                return
            if cursor in seen_cursors:  # a guard, so a bad cursor can't loop forever
                raise RuntimeError("Featurebase returned the same cursor twice, stopping")
            seen_cursors.add(cursor)

    def get_post(self, post_id: str) -> dict:
        return self.http.request("GET", f"/v2/posts/{post_id}")

    def board_names(self) -> dict[str, str]:
        """GET /v2/boards returns every board without pagination."""
        resp = self.http.request("GET", "/v2/boards")
        boards = resp.get("data", []) if isinstance(resp, dict) else (resp or [])
        return {b["id"]: b.get("name") or b["id"] for b in boards if b.get("id")}
