import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbsync.fakes import FakeFeaturebase, FakeNotion  # noqa: E402
from fbsync.featurebase import Featurebase  # noqa: E402
from fbsync.notion import Notion  # noqa: E402
from fbsync.sync import Syncer  # noqa: E402

FB_KEY = "sk_test_SECRET_fb"
NOTION_TOKEN = "ntn_test_SECRET_notion"


def load(name):
    return json.loads((ROOT / "samples" / name).read_text())


@pytest.fixture
def posts():
    return load("posts.json")


@pytest.fixture
def boards():
    return load("boards.json")


@pytest.fixture
def world(posts, boards):
    """Fake Featurebase + fake Notion + the real clients and syncer."""
    fb_fake = FakeFeaturebase(posts, boards)
    notion_fake = FakeNotion()
    fb = Featurebase(FB_KEY, transport=fb_fake, sleep=lambda s: None)
    notion = Notion(NOTION_TOKEN, notion_fake.database_id, transport=notion_fake, sleep=lambda s: None)
    notion.check_schema(fix=True)
    logs = []
    syncer = Syncer(fb, notion, log=logs.append)

    class W:
        pass
    w = W()
    w.fb_fake, w.notion_fake, w.fb, w.notion, w.syncer, w.logs = fb_fake, notion_fake, fb, notion, syncer, logs
    return w


def event(name):
    return load(f"events/{name}")
