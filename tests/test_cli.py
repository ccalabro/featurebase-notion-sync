import os
import subprocess
import sys

from conftest import ROOT


def run(*args, env_extra=None):
    env = {k: v for k, v in os.environ.items()
           if k not in {"FEATUREBASE_API_KEY", "NOTION_TOKEN", "NOTION_DATABASE_ID", "FEATUREBASE_WEBHOOK_SECRET"}}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(ROOT / "sync.py"), *args], capture_output=True,
                          text=True, env=env, cwd=ROOT, timeout=60)


def test_demo_runs_without_keys_and_prints_no_secrets():
    r = run("demo")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "22 created, 0 updated, 0 unchanged, read in 3 Featurebase pages" in out
    assert "0 created, 0 updated, 22 unchanged, Notion writes: 0" in out
    assert "votes: 203 -> 204" in out
    assert "wrong secret: HTTP 401" in out
    for secret in ("sk_demo_not_a_real_key", "ntn_demo_not_a_real_token", "whsec_demo_only"):
        assert secret not in out + r.stderr


def test_missing_config_names_the_variable_not_the_values():
    r = run("backfill", env_extra={"NOTION_TOKEN": "ntn_SHOULD_NOT_PRINT"})
    assert r.returncode != 0
    assert "FEATUREBASE_API_KEY" in r.stderr
    assert "ntn_SHOULD_NOT_PRINT" not in r.stdout + r.stderr
