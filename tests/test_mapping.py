from fbsync.mapping import diff, page_to_row, post_to_row, row_to_properties


def test_post_to_row_uses_documented_fields(posts, boards):
    names = {b["id"]: b["name"] for b in boards}
    row = post_to_row(posts[0], names)
    assert row == {
        "title": "Notion integration",
        "featurebase_id": posts[0]["id"],
        "status": "Planned",
        "votes": 203,
        "board": "Integrations",
        "url": "https://feedback.featurebase.app/p/notion-integration-2",
        "linear": None,
        "jira": None,
        "updated_at": "2026-10-30T08:12:00.000Z",
    }


def test_first_linear_and_jira_link_with_a_url_wins():
    post = {"id": "p1", "title": "t", "integrations": {
        "linear": [{"issueId": "1", "issueUrl": None}, {"issueId": "2", "issueUrl": "https://linear.app/x/issue/A-2"}],
        "jira": [{"issueId": "9", "issueUrl": "https://x.atlassian.net/browse/J-9"}]}}
    row = post_to_row(post, {})
    assert row["linear"] == "https://linear.app/x/issue/A-2"
    assert row["jira"] == "https://x.atlassian.net/browse/J-9"


def test_missing_fields_and_unknown_board_fall_back_safely():
    row = post_to_row({"id": "p1", "boardId": "b-unknown", "title": ""}, {})
    assert row["title"] == "(untitled)"
    assert row["board"] == "b-unknown"
    assert row["status"] is None and row["votes"] is None and row["linear"] is None


def test_select_names_drop_commas_because_notion_rejects_them():
    row = post_to_row({"id": "p", "title": "t", "status": {"name": "Done, shipped"}, "boardId": "b"},
                      {"b": "Ideas, misc"})
    assert row["status"] == "Done  shipped" and row["board"] == "Ideas  misc"


def test_long_values_respect_notion_limits():
    row = post_to_row({"id": "p", "title": "x" * 5000, "postUrl": "https://a/" + "y" * 3000}, {})
    assert len(row["title"]) == 2000 and row["url"] is None


def test_properties_round_trip(posts, boards):
    names = {b["id"]: b["name"] for b in boards}
    row = post_to_row(posts[1], names)
    props = row_to_properties(row, "Name")
    assert props["Name"] == {"title": [{"type": "text", "text": {"content": row["title"]}}]}
    assert props["Votes"] == {"number": row["votes"]}
    assert props["Status"] == {"select": {"name": row["status"]}}
    assert props["Jira"] == {"url": None}
    # what Notion sends back reads into the same row
    page = {"properties": {k: {"type": next(iter(v)), **v} for k, v in props.items()}}
    for v in page["properties"].values():
        for r in v.get("title", []) + v.get("rich_text", []):
            r["plain_text"] = r["text"]["content"]
    assert page_to_row(page, "Name") == row


def test_diff_ignores_equal_timestamps_in_another_format():
    old = {"votes": 1, "updated_at": "2026-10-30T08:12:00.000+00:00"}
    new = {"votes": 2, "updated_at": "2026-10-30T08:12:00.000Z"}
    assert diff(old, new) == {"votes": (1, 2)}
