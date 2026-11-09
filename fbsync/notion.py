"""Notion API: only the calls this tool needs.

Uses the data source model (API version 2025-09-03 and later): a database
holds data sources, and pages live in a data source.
Docs: https://developers.notion.com/reference/intro
"""
from __future__ import annotations

from typing import Iterator

from .http import JsonClient, Transport
from .mapping import KEY_PROPERTY, PROPERTIES

BASE_URL = "https://api.notion.com"
API_VERSION = "2026-03-11"


class SchemaError(Exception):
    pass


class Notion:
    def __init__(self, token: str, database_id: str, data_source_id: str | None = None,
                 transport: Transport | None = None, **kw):
        self.http = JsonClient(
            "Notion", BASE_URL,
            {"Authorization": f"Bearer {token}", "Notion-Version": API_VERSION},
            transport=transport, **kw)
        self.database_id = database_id
        self._data_source_id = data_source_id
        self.title_prop: str | None = None

    # schema

    @property
    def data_source_id(self) -> str:
        if not self._data_source_id:
            db = self.http.request("GET", f"/v1/databases/{self.database_id}")
            sources = db.get("data_sources") or []
            if not sources:
                raise SchemaError("The Notion database has no data source. Is it shared with the connection?")
            if len(sources) > 1:
                print(f"note: the database has {len(sources)} data sources, using the first one "
                      f"({sources[0].get('name')!r}). Set NOTION_DATA_SOURCE_ID to pick another.")
            self._data_source_id = sources[0]["id"]
        return self._data_source_id

    def schema(self) -> dict:
        ds = self.http.request("GET", f"/v1/data_sources/{self.data_source_id}")
        return ds.get("properties") or {}

    def check_schema(self, fix: bool = False) -> list[str]:
        """Returns a list of what was (or would be) added. Raises SchemaError
        when a property exists with another type, because changing a type can
        lose data and I'd rather a person decide that."""
        props = self.schema()
        title = [name for name, p in props.items() if p.get("type") == "title"]
        if not title:
            raise SchemaError("The data source has no title property.")
        self.title_prop = title[0]

        wrong = [f"{name} is {props[name].get('type')}, expected {kind}"
                 for name, kind in PROPERTIES.items()
                 if name in props and props[name].get("type") != kind]
        if wrong:
            raise SchemaError("Property types don't match: " + "; ".join(wrong))

        missing = {name: kind for name, kind in PROPERTIES.items() if name not in props}
        if missing and fix:
            body = {"properties": {name: _schema_for(kind) for name, kind in missing.items()}}
            self.http.request("PATCH", f"/v1/data_sources/{self.data_source_id}", body=body)
        return [f"{name} ({kind})" for name, kind in missing.items()]

    def ensure_ready(self) -> None:
        if self.title_prop is None:
            missing = self.check_schema(fix=False)
            if missing:
                raise SchemaError("Missing properties: " + ", ".join(missing) + ". Run `python3 sync.py setup` first.")

    # pages

    def iter_pages(self) -> Iterator[dict]:
        cursor = None
        while True:
            resp = self.http.request("POST", f"/v1/data_sources/{self.data_source_id}/query",
                                     body=_drop_none({"page_size": 100, "start_cursor": cursor,
                                                      "result_type": "page"}))
            for r in resp.get("results") or []:
                if r.get("object") == "page":
                    yield r
            if not resp.get("has_more"):
                return
            cursor = resp.get("next_cursor")

    def find_by_key(self, featurebase_id: str) -> list[dict]:
        resp = self.http.request(
            "POST", f"/v1/data_sources/{self.data_source_id}/query",
            body={"page_size": 10, "result_type": "page",
                  "filter": {"property": KEY_PROPERTY, "rich_text": {"equals": featurebase_id}}})
        return [r for r in resp.get("results") or [] if r.get("object") == "page"]

    def create_page(self, properties: dict) -> dict:
        return self.http.request("POST", "/v1/pages", body={
            "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
            "properties": properties})

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self.http.request("PATCH", f"/v1/pages/{page_id}", body={"properties": properties})


def _schema_for(kind: str) -> dict:
    return {"number": {"format": "number"}} if kind == "number" else {kind: {}}


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}
