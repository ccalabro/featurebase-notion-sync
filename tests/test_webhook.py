import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import event
from fbsync.webhook import WebhookApp, make_server, sign, verify_signature

SECRET = "whsec_test"
NOW = 1_762_700_000
BODY = b'{"id":"notif_1","topic":"post.created"}'


def headers(body=BODY, secret=SECRET, ts=NOW):
    return {"X-Webhook-Timestamp": str(ts), "X-Webhook-Signature": sign(secret, str(ts), body)}


def test_documented_signature_scheme():
    import hashlib, hmac
    expected = hmac.new(SECRET.encode(), f"{NOW}.".encode() + BODY, hashlib.sha256).hexdigest()
    assert sign(SECRET, str(NOW), BODY) == expected


def test_valid_signature():
    h = headers()
    assert verify_signature(SECRET, BODY, h["X-Webhook-Signature"], h["X-Webhook-Timestamp"], now=NOW)


@pytest.mark.parametrize("case", ["wrong secret", "tampered body", "old", "future", "missing sig",
                                  "missing ts", "non digit ts", "empty secret"])
def test_invalid_signatures(case):
    body, secret_cfg, now = BODY, SECRET, NOW
    h = headers()
    if case == "wrong secret":
        h = headers(secret="whsec_other")
    elif case == "tampered body":
        body = BODY.replace(b"created", b"deleted")
    elif case == "old":
        now = NOW + 301
    elif case == "future":
        now = NOW - 301
    elif case == "missing sig":
        h["X-Webhook-Signature"] = None
    elif case == "missing ts":
        h["X-Webhook-Timestamp"] = None
    elif case == "non digit ts":
        h["X-Webhook-Timestamp"] = "12a"
    elif case == "empty secret":
        secret_cfg = ""
    assert not verify_signature(secret_cfg, body, h["X-Webhook-Signature"], h["X-Webhook-Timestamp"], now=now)


class RecordingSyncer:
    def __init__(self):
        self.events = []

    def handle_event(self, ev):
        self.events.append(ev["id"])
        from fbsync.sync import Result
        return Result("unchanged", ev["id"])


def test_app_rejects_bad_signature_and_queues_nothing():
    s = RecordingSyncer()
    app = WebhookApp(SECRET, s, log=lambda m: None, clock=lambda: NOW)
    assert app.handle(headers(secret="nope"), BODY)[0] == 401
    app.queue.join()
    assert s.events == []


def test_app_skips_duplicate_event_ids_and_headers_are_case_insensitive():
    s = RecordingSyncer()
    app = WebhookApp(SECRET, s, log=lambda m: None, clock=lambda: NOW)
    h = {k.lower(): v for k, v in headers().items()}
    assert app.handle(h, BODY) == (200, "queued")
    assert app.handle(h, BODY) == (200, "duplicate")
    app.queue.join()
    assert s.events == ["notif_1"]


def test_worker_survives_an_error():
    class Boom:
        def handle_event(self, ev):
            raise RuntimeError("notion down")
    logs = []
    app = WebhookApp(SECRET, Boom(), log=logs.append, clock=lambda: NOW)
    app.handle(headers(), BODY)
    app.queue.join()
    assert any("notion down" in m for m in logs)
    assert app._worker.is_alive()


def test_end_to_end_over_a_local_http_server(world, posts):
    """Real HTTP server on 127.0.0.1, fake Featurebase and Notion behind it."""
    world.syncer.backfill()
    app = WebhookApp(SECRET, world.syncer, log=world.logs.append)
    server = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/webhooks/featurebase"

    def post(ev, secret=SECRET):
        import time
        raw = json.dumps(ev).encode()
        ts = str(int(time.time()))
        req = urllib.request.Request(url, data=raw, method="POST", headers={
            "Content-Type": "application/json", "X-Webhook-Timestamp": ts,
            "X-Webhook-Signature": sign(secret, ts, raw)})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        finally:
            app.queue.join()

    try:
        world.fb_fake.posts[posts[0]["id"]]["upvotes"] = 204
        assert post(event("03_post_voted.json")) == 200
        assert post(event("03_post_voted.json"), secret="whsec_bad") == 401
        rows = {r["featurebase_id"]: r for r in world.notion_fake.rows()}
        assert rows[posts[0]["id"]]["votes"] == 204
        assert urllib.request.urlopen(url.replace("/webhooks/featurebase", "/health")).status == 200
    finally:
        server.shutdown()
