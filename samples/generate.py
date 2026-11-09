#!/usr/bin/env python3
"""Writes the fake sample data used by the demo and the tests.

Everything is made up except one row that mirrors the public Notion
integration request, so the demo shows a familiar post. Same seed, same files.
"""
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
rng = random.Random(7)


def oid():
    return "".join(rng.choice("0123456789abcdef") for _ in range(24))


BOARDS = [
    {"object": "board", "id": oid(), "name": "Feature Requests"},
    {"object": "board", "id": oid(), "name": "Integrations"},
    {"object": "board", "id": oid(), "name": "Bugs"},
]
STATUSES = [
    ("In Review", "Gray", "reviewing"), ("Planned", "Blue", "unstarted"),
    ("In Progress", "Yellow", "active"), ("Completed", "Green", "completed"),
    ("Closed", "Red", "canceled"),
]
STATUS_OBJS = {name: {"object": "post_status", "id": oid(), "name": name, "color": color,
                      "type": kind, "isDefault": name == "In Review"}
               for name, color, kind in STATUSES}

TITLES = [
    "Dark mode for the widget", "Export votes to CSV", "SSO with Okta", "Bulk edit post status",
    "Slack digest per board", "Custom domain for the changelog", "Hide vote counts on private boards",
    "Merge duplicate posts from the inbox", "Webhook for comment reactions", "Roadmap embed in our docs",
    "Email login without password", "Translate the portal to Estonian", "Zapier trigger for new votes",
    "Filter roadmap by tag", "API endpoint for survey answers", "Markdown tables in posts",
    "Weekly summary email for admins", "Show MRR next to each voter", "Pin a post to the top of a board",
    "Search inside comments", "Board level permissions for guests",
]


def integrations(linear=None, jira=None):
    return {"linear": linear or [], "jira": jira or [], "clickup": [], "github": [],
            "devops": [], "hubspot": [], "salesforce": []}


def post(title, slug, url, board, status, upvotes, created, updated, integ=None):
    return {
        "object": "post", "id": oid(), "slug": slug, "postUrl": url, "title": title,
        "content": f"<p>{title}.</p>", "boardId": board, "kind": "issue",
        "author": {"id": oid(), "name": "Sample Author", "email": "author@example.com",
                   "profilePicture": None, "type": "customer"},
        "status": STATUS_OBJS[status], "tags": [], "upvotes": upvotes, "commentCount": rng.randint(0, 12),
        "inReview": False, "isPinned": False, "eta": None, "assigneeId": None, "customFields": {},
        "createdAt": created, "updatedAt": updated, "integrations": integ or integrations(),
    }


def main():
    posts = [post("Notion integration", "notion-integration-2",
                  "https://feedback.featurebase.app/p/notion-integration-2",
                  BOARDS[1]["id"], "Planned", 203,
                  "2023-08-14T10:00:00.000Z", "2026-10-30T08:12:00.000Z")]
    for i, title in enumerate(TITLES):
        slug = title.lower().replace(" ", "-")
        board = BOARDS[i % 3]["id"]
        status = STATUSES[rng.randrange(len(STATUSES))][0]
        linear = jira = None
        if i % 4 == 0:
            linear = [{"issueId": f"lin-{i}", "issueUrl": f"https://linear.app/example/issue/FB-{100 + i}"}]
        if i % 7 == 3:
            jira = [{"issueId": str(10000 + i), "issueUrl": f"https://example.atlassian.net/browse/FB-{i}"}]
        day = 1 + i
        posts.append(post(title, slug, f"https://feedback.example.com/p/{slug}", board, status,
                          rng.randint(0, 150), f"2026-0{1 + i % 9}-{day:02d}T09:00:00.000Z",
                          f"2026-10-{day:02d}T12:00:00.000Z", integrations(linear, jira)))

    (HERE / "boards.json").write_text(json.dumps(BOARDS, indent=2) + "\n")
    (HERE / "posts.json").write_text(json.dumps(posts, indent=2) + "\n")

    notion_post = posts[0]
    new_post = post("Two way sync with Notion databases", "two-way-sync-with-notion-databases",
                    "https://feedback.example.com/p/two-way-sync-with-notion-databases",
                    BOARDS[1]["id"], "In Review", 1,
                    "2026-11-09T14:00:00.000Z", "2026-11-09T14:00:00.000Z")
    (HERE / "new_post.json").write_text(json.dumps(new_post, indent=2) + "\n")

    def envelope(eid, topic, item, changes=None):
        data = {"object": "notification_event_data", "item": item}
        if changes is not None:
            data["changes"] = changes
        return {"object": "notification_event", "id": eid, "topic": topic,
                "organizationId": "6595518396205e06b897ad65", "webhookId": "675346db13af7340748ce850",
                "createdAt": "2026-11-09T14:00:00.000Z", "data": data}

    planned, progress = STATUS_OBJS["Planned"], STATUS_OBJS["In Progress"]
    events = {
        "01_post_created.json": envelope("notif_demo-0001", "post.created", new_post),
        "02_status_changed.json": envelope("notif_demo-0002", "post.updated", {**notion_post, "status": progress}, [
            {"field": "status.id", "oldValue": planned["id"], "newValue": progress["id"]},
            {"field": "status.name", "oldValue": "Planned", "newValue": "In Progress"},
            {"field": "status.type", "oldValue": "unstarted", "newValue": "active"},
        ]),
        "03_post_voted.json": envelope("notif_demo-0003", "post.voted", {
            "object": "post_vote", "action": "add", "postId": notion_post["id"],
            "voter": {"object": "contact", "id": oid(), "userId": "user_123", "email": "voter@example.com",
                      "name": "Sample Voter", "profilePicture": None, "type": "customer"}}),
        "04_post_updated_title.json": envelope("notif_demo-0004", "post.updated",
                                               {**new_post, "title": "Two way sync with Notion"}, [
            {"field": "title", "oldValue": new_post["title"], "newValue": "Two way sync with Notion"}]),
        "05_post_deleted.json": envelope("notif_demo-0005", "post.deleted", posts[5]),
    }
    for name, ev in events.items():
        (HERE / "events" / name).write_text(json.dumps(ev, indent=2) + "\n")
    print(f"wrote {len(posts)} posts, {len(BOARDS)} boards, 1 new post and {len(events)} events to {HERE}")


if __name__ == "__main__":
    main()
