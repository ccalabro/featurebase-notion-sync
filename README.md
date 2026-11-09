# Featurebase to Notion sync

I built this because Featurebase feedback and a Notion roadmap drift apart. The Notion integration request on the Featurebase board has 203 votes and has been Planned since August 2023. I think most people asking for it want the same thing: their feedback in a Notion database, next to their specs and roadmap, without copying it by hand. This keeps one Notion row per Featurebase post and updates it when the post changes.

## What it does

It is a one way sync of Featurebase posts into a Notion data source. Each row gets the title, status, votes, board, the post link, the first Linear and Jira links if the post has them, and when the post was last updated in Featurebase.

1. `setup` adds the properties the sync needs to a Notion database, or only checks them with `--check`.
2. `backfill` reads every post through the Featurebase API, page by page, and creates or updates one Notion row per post. Running it twice writes nothing the second time.
3. `serve` runs a small webhook receiver. It checks the Featurebase signature (HMAC SHA256 over `{timestamp}.{raw body}`, hex, in `X-Webhook-Signature`) and updates the matching row on `post.created`, `post.updated` (status changes included), `post.voted` and `post.deleted`.
4. `demo` runs all of the above on fake data, with no keys.

A few choices, and why:

* **The row is keyed by the Featurebase post ID**, in a text property called `Featurebase ID`. Titles change and URLs can change, but the ID doesn't.
* **It only writes when something changed.** It reads the row back and compares first. Notion rate limits are low, and I think a quiet sync is easier to trust.
* **On every webhook it reads the current post from the API**, instead of trusting the event body. Featurebase says events can arrive out of order, and `post.voted` doesn't carry the vote count. One extra GET is cheap and the row always ends up with the latest state.
* **Status and Board are Select properties**, not Notion's Status type. Notion adds a new Select option by itself when a post uses a status it hasn't seen, and Featurebase workspaces have custom statuses.
* **A deleted post keeps its row**, with the status set to `Deleted in Featurebase`. Deleting things in someone's Notion felt like the wrong default.
* **The title property keeps its name.** The tool finds it by type, so an existing database called "Request" or "Name" both work.
* **It never changes a property type.** If `Votes` already exists as text, `setup` stops and says so, because changing a type can lose data.
* **Standard library only.** There is nothing to install to run it. Pytest is the only dependency, it is pinned, and it is only for the tests.

## Setup

You need Python 3.10 or newer.

1. In Featurebase, create an API key in Settings, API.
2. In Notion, create a connection, copy its token, and share the target database with it. The database ID is in the database URL.
3. Set the env vars and run setup, then the backfill:

```
export FEATUREBASE_API_KEY=...
export NOTION_TOKEN=...
export NOTION_DATABASE_ID=...
python3 sync.py setup
python3 sync.py backfill
```

For live updates, run the receiver somewhere with a public https URL, and register `https://your.host/webhooks/featurebase` in Featurebase under Settings, Developers, Webhooks, with the topics `post.created`, `post.updated`, `post.voted` and `post.deleted`. Copy the signing secret:

```
export FEATUREBASE_WEBHOOK_SECRET=whsec_...
python3 sync.py serve --port 8080
```

If the Notion database has more than one data source, set `NOTION_DATA_SOURCE_ID` too. The tool never prints any of these values.

## Demo run

```
python3 sync.py demo
```

It uses 22 fake posts in `samples/`, a fake Featurebase API and a fake Notion database in memory. The webhook part goes through the real HTTP server on 127.0.0.1, with events signed the way Featurebase signs them. Nothing leaves the machine. Trimmed output:

```
== 2. backfill (22 posts, Featurebase pages of 10)
  created   08f86bebb2737f6a6f0fb23c  'Notion integration'
  ...
  22 created, 0 updated, 0 unchanged, read in 3 Featurebase pages

== 3. backfill again, nothing should change
  0 created, 0 updated, 22 unchanged, Notion writes: 0

== 4. webhooks through the real HTTP server on 127.0.0.1
  created   f3f5fb85967f532f3ab3cc2d  'Two way sync with Notion databases'  (post.created)
  updated   08f86bebb2737f6a6f0fb23c  'Notion integration'  status: 'Planned' -> 'In Progress', ...  (post.updated, status changed)
  updated   08f86bebb2737f6a6f0fb23c  'Notion integration'  votes: 203 -> 204  (post.voted, add)
  duplicate notif_demo-0003, skipped
  updated   86702824c1c099724caf4941  (post.deleted, row kept and marked)
  -> wrong secret: HTTP 401
  -> 10 minute old timestamp: HTTP 401
```

All posts are made up except the Notion integration one, which mirrors the public post. To rebuild the samples, run `python3 samples/generate.py`.

To run the tests:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

The tests mock every HTTP call. They cover paging, the mapping, idempotent upserts, valid and invalid signatures, and each event type. One test starts the receiver on 127.0.0.1 and sends it signed requests.

## Assumptions

I read the Featurebase API overview, the List posts, Get a post and List boards references, the webhooks guide and its events reference, and the Notion API reference. Where the docs didn't say, I made a choice and wrote it here.

From the docs:

* Featurebase: base URL `https://do.featurebase.app`, `Authorization: Bearer`, the `Featurebase-Version` header pinned to `2026-08-19.orbit`, `GET /v2/posts` with `limit` up to 100 and `cursor` from `nextCursor` until it is null, `GET /v2/posts/{id}`, `GET /v2/boards`. Post fields: `id`, `title`, `status.name`, `upvotes`, `boardId`, `postUrl`, `updatedAt`, `integrations.linear[].issueUrl`, `integrations.jira[].issueUrl`.
* Webhooks: HMAC SHA256 of `{timestamp}.{raw body}` with the signing secret, as hex, in `X-Webhook-Signature`, with `X-Webhook-Timestamp`, rejected after 5 minutes. Envelope fields `id`, `topic`, `data.item`, `data.changes`. A status change is a `post.updated` with `status.*` entries in `data.changes`. There is no separate status topic.
* Notion: API version `2026-03-11`, the database to data source model, `GET /v1/databases/{id}` for the data source, `GET` and `PATCH /v1/data_sources/{id}` for the schema, `POST /v1/data_sources/{id}/query` with a `rich_text` `equals` filter and `start_cursor`, `POST /v1/pages` with a `data_source_id` parent, `PATCH /v1/pages/{id}`. 2000 characters max for text and URLs, no commas in Select options.

My assumptions:

1. **Boards response.** The List boards example shows a plain array, while other lists use `{ "object": "list", "data": [...] }`. I accept both.
2. **Board name.** A post only has `boardId`, so I read board names once from `GET /v2/boards` and read them again when an unknown ID shows up.
3. **Which posts.** `GET /v2/posts` returns work items by default (`kind=issue`). I don't sync `record` submissions or insights, since they aren't requests people vote on.
4. **More than one Linear or Jira link.** I keep the first one with a URL, because a Notion URL property holds one link.
5. **Empty Select when adding a property.** I send `{"select": {}}` to add a Select with no options yet. The docs show option lists, not the empty case.
6. **Commas in names.** A status or board name with a comma gets the comma replaced by a space, because Notion rejects commas in options.
7. **Duplicate events.** I remember the last 10,000 event IDs in memory. After a restart a duplicate gets through, but the upsert is idempotent, so it only costs a read.

## Limits

* This is not affiliated with Featurebase or Notion. It is a small helper I wrote from their public API docs.
* I haven't tested this against live Featurebase or Notion accounts. Everything runs against fakes built from the docs.
* It is one way, Featurebase to Notion. Edits made in Notion to the synced columns get overwritten on the next change. Other columns you add are left alone.
* Webhooks need Featurebase's Growth plan or higher. On a lower plan, run `backfill` on a schedule instead. It only writes what changed, so every 15 minutes is fine.
* The receiver answers 200 first and writes to Notion after, as Featurebase recommends. If a Notion write fails, the event is not retried, so a periodic backfill is the safety net.
* Featurebase doesn't keep events while a webhook is paused or disabled. A backfill after fixing it catches up.
* Notion queries return at most 10,000 rows. The backfill index would miss rows beyond that, so a very large board needs a different index.
* Comments, tags, ETA and custom fields aren't synced yet. They are easy to add to the mapping once someone says which ones matter.
