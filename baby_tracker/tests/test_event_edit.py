"""Tests for editing/backfilling/deleting events (issue #1 — granularity).

Covers the SQLite backend CRUD (get/update/delete) and the REST endpoints
(POST backfill, PATCH time, DELETE) end-to-end against a temp database.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database


def _db(tmp_path):
    return Database(str(tmp_path / "baby.sqlite"), "America/New_York")


# --- DB layer --------------------------------------------------------------

def test_update_event_changes_time_only(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        t0 = "2026-06-02T18:00:00+00:00"
        eid = await db.insert_event("feed", "breast", "first", t0)
        t1 = "2026-06-02T16:30:00+00:00"
        row = await db.update_event(eid, logged_at=t1)
        assert row["logged_at"] == t1
        assert row["event_subtype"] == "breast"  # untouched
        assert row["note"] == "first"            # untouched
        # get_event reflects the change
        again = await db.get_event(eid)
        assert again["logged_at"] == t1

    asyncio.run(run())


def test_update_event_note_can_be_cleared(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        eid = await db.insert_event("note", None, "typo", "2026-06-02T18:00:00+00:00")
        row = await db.update_event(eid, note=None)
        assert row["note"] is None

    asyncio.run(run())


def test_delete_event(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        eid = await db.insert_event("bath", None, None, "2026-06-02T18:00:00+00:00")
        assert await db.delete_event(eid) is True
        assert await db.get_event(eid) is None
        assert await db.delete_event(eid) is False  # already gone

    asyncio.run(run())


# --- REST endpoints --------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the app at a temp SQLite db and disable MQTT/notify side effects.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")  # MqttBridge.run no-ops without a host
    from app import main
    app = main.create_app(Config.load())
    with TestClient(app) as c:
        yield c


def test_post_backfill_then_patch_then_delete(client):
    # Backfill a missed event at a past time.
    past = "2026-06-02T16:30:00+00:00"
    r = client.post("/api/event", json={"event_type": "feed", "event_subtype": "bottle",
                                        "logged_at": past})
    assert r.status_code == 200
    ev = r.json()["event"]
    assert ev["logged_at"] == past
    eid = ev["id"]

    # It shows up in the log.
    log = client.get("/api/log").json()
    assert any(e["id"] == eid for e in log["entries"])

    # Edit its time.
    fixed = "2026-06-02T15:00:00+00:00"
    r = client.patch(f"/api/event/{eid}", json={"logged_at": fixed})
    assert r.status_code == 200
    assert r.json()["event"]["logged_at"] == fixed

    # Delete it.
    r = client.delete(f"/api/event/{eid}")
    assert r.status_code == 200
    assert client.get("/api/log").json()["entries"] == [] or all(
        e["id"] != eid for e in client.get("/api/log").json()["entries"]
    )


def test_patch_missing_event_404(client):
    assert client.patch("/api/event/99999", json={"logged_at": "2026-06-02T15:00:00+00:00"}).status_code == 404


def test_delete_missing_event_404(client):
    assert client.delete("/api/event/99999").status_code == 404
