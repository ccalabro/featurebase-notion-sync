"""Upsert logic shared by the backfill and the webhook receiver."""
from __future__ import annotations

from dataclasses import dataclass, field

from .featurebase import Featurebase
from .http import ApiError
from .mapping import DELETED_STATUS, diff, page_to_row, post_to_row, row_to_properties
from .notion import Notion

POST_TOPICS = {"post.created", "post.updated", "post.voted"}


@dataclass
class Result:
    action: str          # created, updated, unchanged, skipped, ignored
    post_id: str | None = None
    title: str | None = None
    changes: dict = field(default_factory=dict)
    note: str = ""

    def line(self) -> str:
        head = f"{self.action:<9} {self.post_id or '-'}"
        if self.title:
            head += f"  {self.title!r}"
        if self.changes:
            head += "  " + ", ".join(f"{k}: {o!r} -> {n!r}" for k, (o, n) in self.changes.items())
        if self.note:
            head += f"  ({self.note})"
        return head


class Syncer:
    def __init__(self, featurebase: Featurebase, notion: Notion, log=print):
        self.fb = featurebase
        self.notion = notion
        self.log = log
        self._boards: dict[str, str] | None = None

    def boards(self, board_id: str | None = None) -> dict[str, str]:
        if self._boards is None or (board_id and board_id not in self._boards):
            self._boards = self.fb.board_names()
        return self._boards

    def upsert(self, post: dict, index: dict | None = None) -> Result:
        self.notion.ensure_ready()
        title_prop = self.notion.title_prop
        row = post_to_row(post, self.boards(post.get("boardId")))
        key = row["featurebase_id"]

        if index is not None:
            page = index.get(key)
        else:
            found = self.notion.find_by_key(key)
            if len(found) > 1:
                self.log(f"warning: {len(found)} Notion pages share Featurebase ID {key}, updating the first")
            page = found[0] if found else None

        if page is None:
            created = self.notion.create_page(row_to_properties(row, title_prop))
            if index is not None:
                index[key] = created
            return Result("created", key, row["title"])

        changes = diff(page_to_row(page, title_prop), row)
        if not changes:
            return Result("unchanged", key, row["title"])
        updated = self.notion.update_page(page["id"], row_to_properties(row, title_prop))
        if index is not None:
            index[key] = updated
        return Result("updated", key, row["title"], changes)

    def backfill(self, page_size: int = 100) -> dict[str, int]:
        self.notion.ensure_ready()
        title_prop = self.notion.title_prop
        index: dict[str, dict] = {}
        for page in self.notion.iter_pages():
            key = page_to_row(page, title_prop).get("featurebase_id")
            if not key:
                continue
            if key in index:
                self.log(f"warning: more than one Notion page has Featurebase ID {key}, using the first")
                continue
            index[key] = page

        counts = {"created": 0, "updated": 0, "unchanged": 0}
        seen = set()
        for post in self.fb.iter_posts(limit=page_size):
            if post["id"] in seen:  # a post can show up twice if the list shifts while paging
                continue
            seen.add(post["id"])
            res = self.upsert(post, index)
            counts[res.action] += 1
            if res.action != "unchanged":
                self.log(res.line())
        return counts

    def handle_event(self, event: dict) -> Result:
        topic = event.get("topic")
        item = (event.get("data") or {}).get("item") or {}

        if topic == "post.deleted":
            return self._mark_deleted(item.get("id"))
        if topic not in POST_TOPICS:
            return Result("ignored", note=f"topic {topic}")

        post_id = item.get("postId") if topic == "post.voted" else item.get("id")
        if not post_id:
            return Result("skipped", note=f"{topic} without a post id")

        # Events can arrive out of order, and post.voted carries no vote
        # count, so I read the current post from the API every time.
        try:
            post = self.fb.get_post(post_id)
        except ApiError as e:
            if e.status == 404:
                return Result("skipped", post_id, note="post no longer exists in Featurebase")
            raise

        res = self.upsert(post)
        res.note = _describe(topic, event)
        return res

    def _mark_deleted(self, post_id: str | None) -> Result:
        if not post_id:
            return Result("skipped", note="post.deleted without a post id")
        self.notion.ensure_ready()
        found = self.notion.find_by_key(post_id)
        if not found:
            return Result("unchanged", post_id, note="post.deleted, no Notion row")
        page = found[0]
        if page_to_row(page, self.notion.title_prop).get("status") == DELETED_STATUS:
            return Result("unchanged", post_id, note="post.deleted, already marked")
        self.notion.update_page(page["id"], {"Status": {"select": {"name": DELETED_STATUS}}})
        return Result("updated", post_id, note="post.deleted, row kept and marked")


def _describe(topic: str, event: dict) -> str:
    if topic == "post.updated":
        fields = {c.get("field", "").split(".")[0] for c in (event.get("data") or {}).get("changes") or []}
        if "status" in fields:
            return "post.updated, status changed"
    if topic == "post.voted":
        return f"post.voted, {((event.get('data') or {}).get('item') or {}).get('action', '?')}"
    return topic
