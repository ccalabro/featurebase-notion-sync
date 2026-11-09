"""Featurebase webhook receiver.

Signature check as documented: HMAC-SHA256 over "{timestamp}.{raw body}"
with the signing secret, hex, compared in constant time, and the timestamp
must be within 5 minutes.
Docs: https://help.featurebase.app/articles/1754068-receive-events-with-webhooks
"""
from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOLERANCE_SECONDS = 300
MAX_BODY = 1_000_000


def sign(secret: str, timestamp: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, raw_body: bytes, signature: str | None, timestamp: str | None,
                     now: float | None = None) -> bool:
    if not secret or not signature or not timestamp or not timestamp.isdigit():
        return False
    now = time.time() if now is None else now
    if abs(now - int(timestamp)) > TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, timestamp, raw_body), signature)


class SeenEvents:
    """Remembers the last N event IDs. Featurebase can send the same event
    more than once. The upsert is idempotent anyway, this just saves calls."""

    def __init__(self, maxsize: int = 10_000):
        self.maxsize = maxsize
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, event_id: str) -> bool:
        """True if the ID is new."""
        with self._lock:
            if event_id in self._ids:
                return False
            self._ids[event_id] = None
            if len(self._ids) > self.maxsize:
                self._ids.popitem(last=False)
            return True


class WebhookApp:
    """Verifies, then queues. One worker thread does the Notion writes, so
    events for the same post never race each other."""

    def __init__(self, secret: str, syncer, log=print, clock=time.time):
        self._secret = secret
        self.syncer = syncer
        self.log = log
        self.clock = clock
        self.seen = SeenEvents()
        self.queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

    def handle(self, headers: dict, raw_body: bytes) -> tuple[int, str]:
        h = {k.lower(): v for k, v in headers.items()}
        if not verify_signature(self._secret, raw_body, h.get("x-webhook-signature"),
                                h.get("x-webhook-timestamp"), now=self.clock()):
            self.log("rejected  bad or old signature")
            return 401, "invalid signature"
        try:
            event = json.loads(raw_body)
        except ValueError:
            return 400, "invalid json"
        event_id = event.get("id")
        if event_id and not self.seen.add(event_id):
            self.log(f"duplicate {event_id}, skipped")
            return 200, "duplicate"
        self.queue.put(event)
        return 200, "queued"

    def _work(self) -> None:
        while True:
            event = self.queue.get()
            try:
                res = self.syncer.handle_event(event)
                self.log(res.line())
            except Exception as e:  # keep the worker alive, the backfill can repair later
                self.log(f"error     {event.get('id')}: {e}")
            finally:
                self.queue.task_done()


def make_server(app: WebhookApp, host: str, port: int, path: str = "/webhooks/featurebase"):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != path:
                return self._reply(404, "not found")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._reply(413, "too large")
            raw = self.rfile.read(length)
            status, msg = app.handle(dict(self.headers.items()), raw)
            self._reply(status, msg)

        def do_GET(self):
            self._reply(200 if self.path == "/health" else 404, "ok" if self.path == "/health" else "not found")

        def _reply(self, status: int, msg: str):
            body = json.dumps({"result": msg}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet, the app logs what matters
            pass

    return ThreadingHTTPServer((host, port), Handler)
