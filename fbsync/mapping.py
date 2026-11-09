"""Featurebase post -> one flat row -> Notion property values, and back."""
from __future__ import annotations

# Notion property name -> Notion type. The title property is found by type,
# so it keeps whatever name the database already uses.
PROPERTIES = {
    "Featurebase ID": "rich_text",
    "Status": "select",
    "Votes": "number",
    "Board": "select",
    "Featurebase URL": "url",
    "Linear": "url",
    "Jira": "url",
    "Featurebase updated": "date",
}
KEY_PROPERTY = "Featurebase ID"
DELETED_STATUS = "Deleted in Featurebase"

# row field -> Notion property name
FIELDS = {
    "featurebase_id": "Featurebase ID",
    "status": "Status",
    "votes": "Votes",
    "board": "Board",
    "url": "Featurebase URL",
    "linear": "Linear",
    "jira": "Jira",
    "updated_at": "Featurebase updated",
}
TEXT_LIMIT = 2000  # Notion's limit for text.content and for URLs


def _first_issue_url(post: dict, tool: str) -> str | None:
    for link in ((post.get("integrations") or {}).get(tool) or []):
        url = link.get("issueUrl")
        if url:
            return url
    return None


def _select_name(value: str | None) -> str | None:
    if not value:
        return None
    # Notion option names can't contain commas.
    return value.replace(",", " ").strip()[:100] or None


def post_to_row(post: dict, boards: dict[str, str]) -> dict:
    board_id = post.get("boardId")
    status = post.get("status") or {}
    return {
        "title": (post.get("title") or "(untitled)")[:TEXT_LIMIT],
        "featurebase_id": post["id"],
        "status": _select_name(status.get("name")),
        "votes": post.get("upvotes"),
        "board": _select_name(boards.get(board_id, board_id)),
        "url": _url(post.get("postUrl")),
        "linear": _url(_first_issue_url(post, "linear")),
        "jira": _url(_first_issue_url(post, "jira")),
        "updated_at": post.get("updatedAt"),
    }


def _url(u: str | None) -> str | None:
    return u if u and len(u) <= TEXT_LIMIT else None


def _rich(text: str) -> list:
    return [{"type": "text", "text": {"content": text}}]


def row_to_properties(row: dict, title_prop: str) -> dict:
    props = {title_prop: {"title": _rich(row["title"])}}
    for field, name in FIELDS.items():
        value = row.get(field)
        kind = PROPERTIES[name]
        if kind == "rich_text":
            props[name] = {"rich_text": _rich(value) if value else []}
        elif kind == "select":
            props[name] = {"select": {"name": value} if value else None}
        elif kind == "number":
            props[name] = {"number": value}
        elif kind == "url":
            props[name] = {"url": value}
        elif kind == "date":
            props[name] = {"date": {"start": value} if value else None}
    return props


def _plain(rich: list | None) -> str:
    return "".join(r.get("plain_text") or (r.get("text") or {}).get("content", "") for r in rich or [])


def page_to_row(page: dict, title_prop: str) -> dict:
    """Read a Notion page back into the same row shape, to compare."""
    p = page.get("properties") or {}
    row = {"title": _plain((p.get(title_prop) or {}).get("title"))}
    for field, name in FIELDS.items():
        v = p.get(name) or {}
        kind = PROPERTIES[name]
        if kind == "rich_text":
            row[field] = _plain(v.get("rich_text")) or None
        elif kind == "select":
            row[field] = (v.get("select") or {}).get("name")
        elif kind == "date":
            row[field] = (v.get("date") or {}).get("start")
        else:
            row[field] = v.get(kind)
    return row


def diff(old: dict, new: dict) -> dict:
    """Fields that change, as {field: (old, new)}. Dates compare to the minute.
    Notion stores a date property that way, and it may also rewrite the format."""
    out = {}
    for k, v in new.items():
        before = old.get(k)
        if k == "updated_at" and before and v and _same_instant(before, v):
            continue
        if before != v:
            out[k] = (before, v)
    return out


def _same_instant(a: str, b: str) -> bool:
    """True when both times fall in the same minute.

    A Featurebase time such as 2026-11-09T23:08:08.241Z comes back from Notion
    as 2026-11-09T23:08:00.000+00:00. Seconds are not a real change, because
    writing them again stores the same minute. A later minute still differs.
    Other fields are compared on their own, so an edit in that minute is kept.
    """
    from datetime import datetime
    try:
        pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return a == b
    return pa.replace(second=0, microsecond=0) == pb.replace(second=0, microsecond=0)
