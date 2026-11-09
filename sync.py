#!/usr/bin/env python3
"""Featurebase to Notion sync.

  python3 sync.py setup [--check]   create or check the Notion properties
  python3 sync.py backfill          copy every Featurebase post into Notion
  python3 sync.py serve             receive Featurebase webhooks
  python3 sync.py demo              run everything on fake data, no keys

Config comes from env vars: FEATUREBASE_API_KEY, NOTION_TOKEN,
NOTION_DATABASE_ID, FEATUREBASE_WEBHOOK_SECRET (serve only), and optionally
NOTION_DATA_SOURCE_ID. Values are never printed.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from fbsync.featurebase import Featurebase
from fbsync.notion import Notion, SchemaError
from fbsync.sync import Syncer
from fbsync.webhook import WebhookApp, make_server, sign

ROOT = Path(__file__).resolve().parent


def need(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit("missing env vars: " + ", ".join(missing))
    return {n: os.environ[n] for n in names}


def notion_from_env() -> Notion:
    env = need("NOTION_TOKEN", "NOTION_DATABASE_ID")
    return Notion(env["NOTION_TOKEN"], env["NOTION_DATABASE_ID"],
                  data_source_id=os.environ.get("NOTION_DATA_SOURCE_ID") or None)


def featurebase_from_env() -> Featurebase:
    return Featurebase(need("FEATUREBASE_API_KEY")["FEATUREBASE_API_KEY"])


def cmd_setup(args) -> None:
    notion = notion_from_env()
    missing = notion.check_schema(fix=not args.check)
    if not missing:
        print(f"Notion database is ready. Title property: {notion.title_prop!r}")
    elif args.check:
        sys.exit("missing properties: " + ", ".join(missing) + ". Run `python3 sync.py setup` to add them.")
    else:
        print("added properties: " + ", ".join(missing))


def cmd_backfill(args) -> None:
    syncer = Syncer(featurebase_from_env(), notion_from_env())
    counts = syncer.backfill()
    print(_summary(counts))


def cmd_serve(args) -> None:
    secret = need("FEATUREBASE_WEBHOOK_SECRET")["FEATUREBASE_WEBHOOK_SECRET"]
    syncer = Syncer(featurebase_from_env(), notion_from_env())
    syncer.notion.ensure_ready()  # fail at start, not on the first event
    app = WebhookApp(secret, syncer)
    server = make_server(app, args.host, args.port)
    print(f"listening on http://{args.host}:{args.port}/webhooks/featurebase")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def _summary(c: dict) -> str:
    return f"{c['created']} created, {c['updated']} updated, {c['unchanged']} unchanged"


def cmd_demo(args) -> None:
    from fbsync.fakes import FakeFeaturebase, FakeNotion

    posts = json.loads((ROOT / "samples/posts.json").read_text())
    boards = json.loads((ROOT / "samples/boards.json").read_text())
    fb_fake = FakeFeaturebase(posts, boards)
    notion_fake = FakeNotion()
    fb = Featurebase("sk_demo_not_a_real_key", transport=fb_fake)
    notion = Notion("ntn_demo_not_a_real_token", notion_fake.database_id, transport=notion_fake)
    log = lambda s: print("  " + s)
    syncer = Syncer(fb, notion, log=log)

    print("== 1. setup on an empty Notion database")
    print("  added: " + ", ".join(notion.check_schema(fix=True)))
    print("  second check, missing: " + (", ".join(notion.check_schema()) or "nothing"))

    print(f"\n== 2. backfill ({len(posts)} posts, Featurebase pages of {args.page_size})")
    counts = syncer.backfill(page_size=args.page_size)
    pages = sum(1 for c in fb_fake.calls if c == "GET /v2/posts")
    print(f"  {_summary(counts)}, read in {pages} Featurebase pages")

    print("\n== 3. backfill again, nothing should change")
    fb_fake.calls.clear()
    before = len(notion_fake.writes)
    print("  " + _summary(syncer.backfill(page_size=args.page_size)) + f", Notion writes: {len(notion_fake.writes) - before}")

    print("\n== 4. webhooks through the real HTTP server on 127.0.0.1")
    secret = "whsec_demo_only"
    app = WebhookApp(secret, syncer, log=log)
    server = make_server(app, "127.0.0.1", 0)
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/webhooks/featurebase"

    def send(event: dict, *, secret_used=secret, age=0, label=""):
        raw = json.dumps(event).encode()
        ts = str(int(time.time()) - age)
        req = urllib.request.Request(url, data=raw, method="POST", headers={
            "Content-Type": "application/json", "X-Webhook-Timestamp": ts,
            "X-Webhook-Signature": sign(secret_used, ts, raw)})
        try:
            with urllib.request.urlopen(req) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        app.queue.join()
        print(f"  -> {label or event['topic']}: HTTP {status}")

    ev = {p.name: json.loads(p.read_text()) for p in sorted((ROOT / "samples/events").glob("*.json"))}
    notion_id = posts[0]["id"]

    # Each step first changes the fake Featurebase, then sends the event it would send.
    fb_fake.add(json.loads((ROOT / "samples/new_post.json").read_text()))
    send(ev["01_post_created.json"])

    fb_fake.posts[notion_id]["status"] = ev["02_status_changed.json"]["data"]["item"]["status"]
    fb_fake.posts[notion_id]["updatedAt"] = "2026-11-09T14:05:00.000Z"
    send(ev["02_status_changed.json"], label="post.updated (status)")

    fb_fake.posts[notion_id]["upvotes"] += 1
    send(ev["03_post_voted.json"])

    send(ev["03_post_voted.json"], label="same post.voted again")

    new_id = ev["01_post_created.json"]["data"]["item"]["id"]
    fb_fake.posts[new_id]["title"] = "Two way sync with Notion"
    send(ev["04_post_updated_title.json"], label="post.updated (title)")

    deleted_id = ev["05_post_deleted.json"]["data"]["item"]["id"]
    fb_fake.posts.pop(deleted_id)
    fb_fake.order.remove(deleted_id)
    send(ev["05_post_deleted.json"])

    forged = copy.deepcopy(ev["03_post_voted.json"]); forged["id"] = "notif_forged"
    send(forged, secret_used="whsec_wrong", label="wrong secret")
    old = copy.deepcopy(ev["03_post_voted.json"]); old["id"] = "notif_old"
    send(old, age=600, label="10 minute old timestamp")
    server.shutdown()

    print("\n== 5. Notion rows now (top 6 by votes)")
    rows = sorted(notion_fake.rows(notion.title_prop), key=lambda r: -(r["votes"] or 0))
    print(f"  {'Votes':>5}  {'Status':<22} {'Board':<17} {'Linear':<6} Title")
    for r in rows[:6]:
        print(f"  {r['votes']:>5}  {r['status'] or '':<22} {r['board'] or '':<17} {'yes' if r['linear'] else '':<6} {r['title']}")
    linear = sum(1 for r in rows if r["linear"]); jira = sum(1 for r in rows if r["jira"])
    print(f"  ... {len(rows)} rows in total, {linear} with a Linear link, {jira} with a Jira link")
    print(f"  {len(notion_fake.writes)} writes to the fake Notion, no calls to the real Featurebase or Notion")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="create or check the Notion properties")
    s.add_argument("--check", action="store_true", help="only check, change nothing")
    s.set_defaults(fn=cmd_setup)
    sub.add_parser("backfill", help="copy every Featurebase post into Notion").set_defaults(fn=cmd_backfill)
    s = sub.add_parser("serve", help="receive Featurebase webhooks")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("demo", help="run on fake data, no keys needed")
    s.add_argument("--page-size", type=int, default=10)
    s.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except SchemaError as e:
        sys.exit(f"Notion schema problem: {e}")


if __name__ == "__main__":
    main()
