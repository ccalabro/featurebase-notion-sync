import pytest

from conftest import NOTION_TOKEN
from fbsync.fakes import FakeNotion
from fbsync.mapping import PROPERTIES
from fbsync.notion import API_VERSION, Notion, SchemaError


def make(fake):
    return Notion(NOTION_TOKEN, fake.database_id, transport=fake, sleep=lambda s: None)


def test_setup_adds_missing_properties_and_finds_the_title_by_type():
    fake = FakeNotion(title_name="Request")
    n = make(fake)
    added = n.check_schema(fix=True)
    assert len(added) == len(PROPERTIES)
    assert n.title_prop == "Request"
    assert {k: v["type"] for k, v in fake.schema.items() if k != "Request"} == PROPERTIES
    assert make(fake).check_schema(fix=True) == []  # second run changes nothing
    assert fake.writes == ["schema"]


def test_check_only_does_not_write():
    fake = FakeNotion()
    assert make(fake).check_schema(fix=False)
    assert fake.writes == []


def test_wrong_property_type_is_reported_not_changed():
    fake = FakeNotion()
    fake.schema["Votes"] = {"id": "v", "name": "Votes", "type": "rich_text", "rich_text": {}}
    with pytest.raises(SchemaError, match="Votes is rich_text, expected number"):
        make(fake).check_schema(fix=True)
    assert fake.writes == []


def test_backfill_refuses_to_run_before_setup():
    with pytest.raises(SchemaError, match="Run `python3 sync.py setup`"):
        make(FakeNotion()).ensure_ready()


def test_resolves_data_source_from_database_and_sends_version():
    seen = []
    fake = FakeNotion()

    def transport(method, url, headers, body):
        seen.append((method, url, headers.get("Notion-Version")))
        return fake(method, url, headers, body)

    Notion(NOTION_TOKEN, fake.database_id, transport=transport).schema()
    assert seen[0] == ("GET", f"https://api.notion.com/v1/databases/{fake.database_id}", API_VERSION)
    assert seen[1][1].endswith(f"/v1/data_sources/{fake.data_source_id}")


def test_iter_pages_follows_next_cursor():
    fake = FakeNotion()
    n = make(fake)
    n.check_schema(fix=True)
    for i in range(230):
        n.create_page({"Name": {"title": [{"type": "text", "text": {"content": f"p{i}"}}]}})
    assert len(list(n.iter_pages())) == 230
