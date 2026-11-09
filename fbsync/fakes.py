"""In-memory stand-ins for the Featurebase and Notion APIs.

They plug in as transports, so the real client code runs end to end in the
demo and the tests, without keys and without network.
"""
from __future__ import annotations

import base64
import copy
import json
import uuid
from urllib.parse import parse_qs, urlsplit


def _ok(payload, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(payload).encode()


class FakeFeaturebase:
    def __init__(self, posts: list[dict], boards: list[dict]):
        self.posts = {p["id"]: copy.deepcopy(p) for p in posts}
        self.order = [p["id"] for p in posts]
        self.boards = boards
        self.calls: list[str] = []

    def add(self, post: dict) -> None:
        if post["id"] not in self.posts:
            self.order.append(post["id"])
        self.posts[post["id"]] = copy.deepcopy(post)

    def __call__(self, method, url, headers, body):
        parts = urlsplit(url)
        self.calls.append(f"{method} {parts.path}")
        if not headers.get("Authorization", "").startswith("Bearer "):
            return _ok({"error": {"type": "auth", "code": "unauthorized", "message": "no key"}}, 401)
        q = parse_qs(parts.query)
        if parts.path == "/v2/boards":
            return _ok(self.boards)
        if parts.path == "/v2/posts":
            limit = int(q.get("limit", ["10"])[0])
            start = int(base64.b64decode(q["cursor"][0])) if "cursor" in q else 0
            ids = self.order[start:start + limit]
            nxt = start + limit
            cursor = base64.b64encode(str(nxt).encode()).decode() if nxt < len(self.order) else None
            return _ok({"object": "list", "data": [self.posts[i] for i in ids], "nextCursor": cursor})
        if parts.path.startswith("/v2/posts/"):
            pid = parts.path.rsplit("/", 1)[1]
            if pid not in self.posts:
                return _ok({"error": {"type": "invalid_request", "code": "not_found", "message": "Post not found"}}, 404)
            return _ok(self.posts[pid])
        return _ok({"error": {"code": "not_found", "message": "no route"}}, 404)


class FakeNotion:
    """Just enough of Notion's data source model for this tool."""

    def __init__(self, database_id="db_demo", data_source_id="ds_demo", title_name="Name"):
        self.database_id = database_id
        self.data_source_id = data_source_id
        self.schema = {title_name: {"id": "title", "name": title_name, "type": "title", "title": {}}}
        self.pages: dict[str, dict] = {}
        self.writes: list[str] = []  # every create/update/schema change, for tests

    def __call__(self, method, url, headers, body):
        path = urlsplit(url).path
        data = json.loads(body) if body else {}
        if not headers.get("Notion-Version"):
            return _ok({"object": "error", "code": "missing_version", "message": "Notion-Version header missing"}, 400)
        if method == "GET" and path == f"/v1/databases/{self.database_id}":
            return _ok({"object": "database", "id": self.database_id,
                        "data_sources": [{"id": self.data_source_id, "name": "Featurebase posts"}]})
        if path == f"/v1/data_sources/{self.data_source_id}":
            if method == "PATCH":
                for name, spec in data["properties"].items():
                    kind = next(iter(spec))
                    self.schema[name] = {"id": name[:4], "name": name, "type": kind, kind: spec[kind]}
                self.writes.append("schema")
            return _ok({"object": "data_source", "id": self.data_source_id, "properties": self.schema})
        if method == "POST" and path == f"/v1/data_sources/{self.data_source_id}/query":
            return _ok(self._query(data))
        if method == "POST" and path == "/v1/pages":
            if data["parent"].get("data_source_id") != self.data_source_id:
                return _ok({"object": "error", "code": "validation_error", "message": "bad parent"}, 400)
            err = self._check(data["properties"])
            if err:
                return _ok({"object": "error", "code": "validation_error", "message": err}, 400)
            page = {"object": "page", "id": str(uuid.uuid4()), "in_trash": False,
                    "properties": self._store(data["properties"], {})}
            self.pages[page["id"]] = page
            self.writes.append(f"create {page['id']}")
            return _ok(page)
        if method == "PATCH" and path.startswith("/v1/pages/"):
            pid = path.rsplit("/", 1)[1]
            if pid not in self.pages:
                return _ok({"object": "error", "code": "object_not_found", "message": "no page"}, 404)
            err = self._check(data["properties"])
            if err:
                return _ok({"object": "error", "code": "validation_error", "message": err}, 400)
            page = self.pages[pid]
            page["properties"] = self._store(data["properties"], page["properties"])
            self.writes.append(f"update {pid}")
            return _ok(page)
        return _ok({"object": "error", "code": "invalid_request_url", "message": f"{method} {path}"}, 400)

    def _check(self, props):
        for name in props:
            if name not in self.schema:
                return f"{name} is not a property that exists."
        return None

    def _store(self, props, existing):
        out = copy.deepcopy(existing)
        for name, value in props.items():
            kind = self.schema[name]["type"]
            v = copy.deepcopy(value[kind])
            if kind in ("title", "rich_text"):
                for r in v:
                    r["plain_text"] = r["text"]["content"]
            if kind == "select" and v:
                opts = self.schema[name][kind].setdefault("options", [])
                if v["name"] not in [o["name"] for o in opts]:
                    opts.append({"name": v["name"], "color": "default"})
            out[name] = {"id": self.schema[name]["id"], "type": kind, kind: v}
        return out

    def _query(self, data):
        rows = list(self.pages.values())
        f = data.get("filter")
        if f:
            want = f["rich_text"]["equals"]
            rows = [p for p in rows
                    if "".join(r["plain_text"] for r in (p["properties"].get(f["property"]) or {}).get("rich_text", [])) == want]
        size = data.get("page_size", 100)
        start = int(data.get("start_cursor") or 0)
        chunk = rows[start:start + size]
        more = start + size < len(rows)
        return {"object": "list", "results": copy.deepcopy(chunk),
                "has_more": more, "next_cursor": str(start + size) if more else None}

    def rows(self, title_prop="Name"):
        from .mapping import page_to_row
        return [page_to_row(p, title_prop) for p in self.pages.values()]
