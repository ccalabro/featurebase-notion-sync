import copy

from conftest import event
from fbsync.mapping import DELETED_STATUS


def rows_by_id(world):
    return {r["featurebase_id"]: r for r in world.notion_fake.rows(world.notion.title_prop)}


def test_backfill_creates_one_row_per_post(world, posts):
    counts = world.syncer.backfill(page_size=7)
    assert counts == {"created": len(posts), "updated": 0, "unchanged": 0}
    rows = rows_by_id(world)
    assert set(rows) == {p["id"] for p in posts}
    assert rows[posts[0]["id"]]["votes"] == 203


def test_backfill_twice_never_duplicates_or_writes(world, posts):
    world.syncer.backfill()
    writes = len(world.notion_fake.writes)
    assert world.syncer.backfill() == {"created": 0, "updated": 0, "unchanged": len(posts)}
    assert len(world.notion_fake.writes) == writes
    assert len(world.notion_fake.pages) == len(posts)


def test_second_backfill_treats_notion_minute_dates_as_unchanged(world, posts):
    # The live bug: Featurebase has seconds, Notion stores the minute.
    # 45 seconds would become the next minute if this rounded instead of dropping them.
    post = world.fb_fake.posts[posts[0]["id"]]
    post["updatedAt"] = "2026-11-09T23:08:45.100Z"
    world.syncer.backfill()
    stored = rows_by_id(world)[posts[0]["id"]]["updated_at"]
    assert stored == "2026-11-09T23:08:00.000+00:00"
    writes = len(world.notion_fake.writes)
    assert world.syncer.backfill() == {"created": 0, "updated": 0, "unchanged": len(posts)}
    assert len(world.notion_fake.writes) == writes


def test_same_minute_vote_still_updates_the_row(world, posts):
    post = world.fb_fake.posts[posts[0]["id"]]
    post["updatedAt"] = "2026-11-09T23:08:08.241Z"
    world.syncer.backfill()
    post["upvotes"] = 204
    post["updatedAt"] = "2026-11-09T23:08:45.000Z"
    world.logs.clear()
    counts = world.syncer.backfill()
    assert counts == {"created": 0, "updated": 1, "unchanged": len(posts) - 1}
    assert any("votes: 203 -> 204" in line for line in world.logs)
    assert not any("updated_at" in line for line in world.logs)


def test_a_later_minute_updates_the_row(world, posts):
    world.syncer.backfill()
    world.fb_fake.posts[posts[0]["id"]]["updatedAt"] = "2026-11-09T23:09:01.000Z"
    world.logs.clear()
    counts = world.syncer.backfill()
    assert counts == {"created": 0, "updated": 1, "unchanged": len(posts) - 1}
    assert any("updated_at" in line for line in world.logs)


def test_backfill_updates_only_what_changed(world, posts):
    world.syncer.backfill()
    world.fb_fake.posts[posts[0]["id"]]["upvotes"] = 250
    counts = world.syncer.backfill()
    assert counts == {"created": 0, "updated": 1, "unchanged": len(posts) - 1}
    assert any("votes: 203 -> 250" in line for line in world.logs)


def test_post_listed_twice_while_paging_is_written_once(world, posts):
    world.fb_fake.order.insert(15, posts[0]["id"])  # the list shifted between pages
    world.syncer.backfill(page_size=10)
    assert len(world.notion_fake.pages) == len(posts)


def test_single_upsert_without_index_is_idempotent(world, posts):
    a = world.syncer.upsert(posts[3])
    b = world.syncer.upsert(posts[3])
    assert (a.action, b.action) == ("created", "unchanged")
    assert len(world.notion_fake.pages) == 1


def test_rows_added_by_hand_without_an_id_are_left_alone(world, posts):
    world.notion.create_page({"Name": {"title": [{"type": "text", "text": {"content": "my own note"}}]}})
    world.syncer.backfill()
    assert len(world.notion_fake.pages) == len(posts) + 1


# events

def test_post_created_adds_a_row(world, posts):
    world.syncer.backfill()
    ev = event("01_post_created.json")
    world.fb_fake.add(ev["data"]["item"])
    res = world.syncer.handle_event(ev)
    assert res.action == "created"
    assert rows_by_id(world)[ev["data"]["item"]["id"]]["status"] == "In Review"


def test_status_change_updates_status(world, posts):
    world.syncer.backfill()
    ev = event("02_status_changed.json")
    world.fb_fake.posts[posts[0]["id"]]["status"] = ev["data"]["item"]["status"]
    res = world.syncer.handle_event(ev)
    assert res.action == "updated" and res.note == "post.updated, status changed"
    assert rows_by_id(world)[posts[0]["id"]]["status"] == "In Progress"


def test_vote_reads_the_current_count_from_the_api(world, posts):
    world.syncer.backfill()
    world.fb_fake.posts[posts[0]["id"]]["upvotes"] = 204
    res = world.syncer.handle_event(event("03_post_voted.json"))
    assert res.action == "updated" and res.changes == {"votes": (203, 204)}
    assert f"GET /v2/posts/{posts[0]['id']}" in world.fb_fake.calls


def test_vote_for_a_post_not_yet_in_notion_creates_it(world, posts):
    res = world.syncer.handle_event(event("03_post_voted.json"))
    assert res.action == "created" and len(world.notion_fake.pages) == 1


def test_stale_event_payload_does_not_overwrite_newer_state(world, posts):
    world.syncer.backfill()
    ev = copy.deepcopy(event("02_status_changed.json"))
    ev["data"]["item"]["status"]["name"] = "Old status from a late event"
    res = world.syncer.handle_event(ev)  # the API still says Planned
    assert res.action == "unchanged"
    assert rows_by_id(world)[posts[0]["id"]]["status"] == "Planned"


def test_title_update(world):
    ev_new = event("01_post_created.json")
    world.fb_fake.add({**ev_new["data"]["item"], "title": "Two way sync with Notion"})
    res = world.syncer.handle_event(event("04_post_updated_title.json"))
    assert res.action == "created" and res.title == "Two way sync with Notion"


def test_deleted_post_keeps_the_row_and_marks_it(world, posts):
    world.syncer.backfill()
    ev = event("05_post_deleted.json")
    res = world.syncer.handle_event(ev)
    again = world.syncer.handle_event(ev)
    assert (res.action, again.action) == ("updated", "unchanged")
    assert rows_by_id(world)[ev["data"]["item"]["id"]]["status"] == DELETED_STATUS
    assert len(world.notion_fake.pages) == len(posts)


def test_event_for_a_post_gone_from_featurebase_is_skipped(world):
    ev = event("03_post_voted.json")
    world.fb_fake.posts.clear()
    assert world.syncer.handle_event(ev).action == "skipped"


def test_other_topics_are_ignored(world):
    res = world.syncer.handle_event({"id": "notif_x", "topic": "ticket.created", "data": {"item": {}}})
    assert res.action == "ignored" and world.notion_fake.writes == ["schema"]
