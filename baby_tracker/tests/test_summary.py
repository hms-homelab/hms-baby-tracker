"""Tests for AI daily summaries (SDD-003): the de-identified digest (privacy),
cap enforcement, disabled no-op, and the REST endpoints (LLM mocked)."""
import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database
from app import summary, llm


def _db(tmp_path):
    return Database(str(tmp_path / "b.sqlite"), "America/New_York")


def test_digest_excludes_notes_and_names(tmp_path):
    """The prompt sent to any provider must contain ONLY aggregate numbers —
    never a note, special note, or name."""
    db = _db(tmp_path)

    async def run():
        await db.init()
        await db.insert_event("feed", "bottle", "SECRETNAME loves grandma", None)
        await db.insert_event("note", None, "⭐ CONFIDENTIAL family note", None)
        await db.insert_event("temperature", None, "PRIVATE", None, 101.0, "°F")
        cfg = Config()
        digest = await summary.build_digest(db, cfg)
        prompt = summary.build_prompt(cfg, digest)
        for leak in ("SECRETNAME", "CONFIDENTIAL", "PRIVATE", "grandma"):
            assert leak not in prompt, f"{leak} leaked into the prompt"
        assert "Feeds today:" in prompt and "FEVER" in prompt  # numbers/labels present

    asyncio.run(run())


def test_digest_has_3day_trend(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        now = dt.datetime.now(dt.timezone.utc)
        await db.insert_event("feed", "bottle", None, (now - dt.timedelta(days=2)).isoformat())
        await db.insert_event("feed", "breast", None, now.isoformat())
        d = await summary.build_digest(db, Config())
        assert len(d["days_3"]) == 3
        prompt = summary.build_prompt(Config(), d)
        assert "Last 3 days" in prompt

    asyncio.run(run())


def test_generate_stores_and_caps(tmp_path, monkeypatch):
    db = _db(tmp_path)

    async def fake(cfg, prompt, install_token=None):
        return "Calm day, keep it up."
    monkeypatch.setattr(llm, "generate", fake)

    async def run():
        await db.init()
        cfg = Config()
        cfg.summary_enabled = True
        cfg.summary_daily_cap = 2
        r1 = await summary.generate(db, cfg, None, "tok", "manual")
        assert r1["text"] == "Calm day, keep it up." and r1["source"] == "manual"
        await summary.generate(db, cfg, None, "tok", "auto")
        with pytest.raises(summary.CapReached):
            await summary.generate(db, cfg, None, "tok", "manual")
        day = summary._day(cfg, dt.datetime.now(dt.timezone.utc))
        assert await db.count_summaries_today(day) == 2
        assert (await db.latest_summary())["text"] == "Calm day, keep it up."

    asyncio.run(run())


def test_disabled_is_noop(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        cfg = Config()
        cfg.summary_enabled = False
        assert await summary.generate(db, cfg, None, "tok", "auto") is None

    asyncio.run(run())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    monkeypatch.setenv("SUMMARY_ENABLED", "1")

    async def fake(cfg, prompt, install_token=None):
        return "Recap from test."
    monkeypatch.setattr(llm, "generate", fake)
    from app import main
    app = main.create_app(Config.load())
    with TestClient(app) as c:
        yield c


def test_config_reports_summary_enabled(client):
    assert client.get("/api/config").json()["summary_enabled"] is True


def test_summary_endpoints_and_cap(client):
    j = client.get("/api/summary").json()
    assert j["enabled"] and j["used_today"] == 0 and j["can_generate"] and j["cap"] == 2
    assert client.post("/api/summary").json()["summary"]["text"] == "Recap from test."
    client.post("/api/summary")                       # 2/2
    assert client.post("/api/summary").status_code == 429  # capped
    assert client.get("/api/summary").json()["used_today"] == 2


def test_summary_disabled_400(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    monkeypatch.setenv("SUMMARY_ENABLED", "0")
    from app import main
    app = main.create_app(Config.load())
    with TestClient(app) as c:
        assert c.post("/api/summary").status_code == 400
        assert c.get("/api/summary").json()["enabled"] is False
