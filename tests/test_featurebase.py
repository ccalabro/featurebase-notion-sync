import json

import pytest

from conftest import FB_KEY
from fbsync.featurebase import API_VERSION, Featurebase
from fbsync.http import ApiError


def test_backfill_reads_every_page_and_passes_the_cursor(world, posts):
    got = list(world.fb.iter_posts(limit=10))
    assert [p["id"] for p in got] == [p["id"] for p in posts]
    assert world.fb_fake.calls.count("GET /v2/posts") == 3  # 22 posts, pages of 10


def test_request_headers_and_query():
    seen = []

    def transport(method, url, headers, body):
        seen.append((url, headers))
        return 200, {}, json.dumps({"object": "list", "data": [], "nextCursor": None}).encode()

    list(Featurebase(FB_KEY, transport=transport).iter_posts())
    url, headers = seen[0]
    assert url == "https://do.featurebase.app/v2/posts?limit=100"
    assert headers["Authorization"] == f"Bearer {FB_KEY}"
    assert headers["Featurebase-Version"] == API_VERSION


def test_cursor_is_sent_on_the_next_request():
    urls = []
    pages = [{"data": [{"id": "a"}], "nextCursor": "abc=="}, {"data": [{"id": "b"}], "nextCursor": None}]

    def transport(method, url, headers, body):
        urls.append(url)
        return 200, {}, json.dumps(pages[len(urls) - 1]).encode()

    ids = [p["id"] for p in Featurebase(FB_KEY, transport=transport).iter_posts()]
    assert ids == ["a", "b"]
    assert urls[1].endswith("cursor=abc%3D%3D")


def test_same_cursor_twice_stops_instead_of_looping():
    def transport(method, url, headers, body):
        return 200, {}, json.dumps({"data": [{"id": "x"}], "nextCursor": "same"}).encode()

    with pytest.raises(RuntimeError):
        list(Featurebase(FB_KEY, transport=transport).iter_posts())


def test_rate_limit_waits_for_retry_after_then_retries():
    calls, sleeps = [], []

    def transport(method, url, headers, body):
        calls.append(url)
        if len(calls) == 1:
            return 429, {"Retry-After": "7"}, b'{"error":{"code":"rate_limited","message":"slow down"}}'
        return 200, {}, b'{"data": [], "nextCursor": null}'

    list(Featurebase(FB_KEY, transport=transport, sleep=sleeps.append).iter_posts())
    assert sleeps == [7.0] and len(calls) == 2


def test_errors_show_the_api_message_but_never_the_key():
    def transport(method, url, headers, body):
        return 401, {}, b'{"error":{"type":"auth","code":"invalid_api_key","message":"Invalid API key"}}'

    with pytest.raises(ApiError) as e:
        Featurebase(FB_KEY, transport=transport).get_post("p1")
    assert "invalid_api_key" in str(e.value) and FB_KEY not in str(e.value)


@pytest.mark.parametrize("shape", ["array", "envelope"])
def test_board_names_accepts_array_or_list_envelope(boards, shape):
    payload = boards if shape == "array" else {"object": "list", "data": boards}

    def transport(method, url, headers, body):
        return 200, {}, json.dumps(payload).encode()

    names = Featurebase(FB_KEY, transport=transport).board_names()
    assert names[boards[1]["id"]] == "Integrations"
