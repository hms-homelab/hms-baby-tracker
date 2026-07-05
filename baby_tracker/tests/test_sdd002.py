"""Tests for SDD-002 Phase 1: supplies (auto-decrement + reminders), the Get
Ready checklist, the /api/config default_tab, and the `medium` contraction
intensity alias. SQLite backend + REST endpoints against a temp database.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.assessment import _INTENSITY_MAP, _intensity_of
from app.config import Config
from app.db import DEFAULT_CHECKLIST, Database
from app import supplies


def _db(tmp_path):
    return Database(str(tmp_path / "baby.sqlite"), "America/New_York")


# --- intensity alias -------------------------------------------------------

def test_medium_intensity_alias():
    assert _INTENSITY_MAP["medium"] == _INTENSITY_MAP["moderate"] == 2
    assert _intensity_of({"event_subtype": "medium"}) == "medium"


# --- supplies DB + consumption logic --------------------------------------

def test_checklist_seeded_on_init(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        items = await db.list_checklist()
        assert [i["label"] for i in items] == DEFAULT_CHECKLIST
        # idempotent: init again does not duplicate
        await db.init()
        assert len(await db.list_checklist()) == len(DEFAULT_CHECKLIST)

    asyncio.run(run())


def test_auto_decrement_and_low_crossing(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        s = await db.insert_supply({
            "category": "formula", "name": "Formula", "quantity": 3,
            "low_threshold": 2, "consume_event_type": "feed",
            "consume_event_subtype": "bottle", "consume_amount": 1,
        })
        # A matching event decrements and crosses the low threshold (3 -> 2).
        crossed = await supplies.apply_consumption(db, "feed", "bottle")
        assert len(crossed) == 1 and crossed[0]["id"] == s["id"]
        got = await db.get_supply(s["id"])
        assert got["quantity"] == 2 and got["low_notified"] == 1
        # Second matching event: already-notified, so it does NOT re-cross.
        crossed2 = await supplies.apply_consumption(db, "feed", "bottle")
        assert crossed2 == []
        assert (await db.get_supply(s["id"]))["quantity"] == 1
        # A non-matching subtype leaves it alone.
        await supplies.apply_consumption(db, "feed", "breast")
        assert (await db.get_supply(s["id"]))["quantity"] == 1

    asyncio.run(run())


def test_consumption_clamps_at_zero(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        s = await db.insert_supply({
            "name": "Wipes", "quantity": 1, "consume_event_type": "diaper",
            "consume_amount": 5,
        })
        await supplies.apply_consumption(db, "diaper", "pee")
        assert (await db.get_supply(s["id"]))["quantity"] == 0

    asyncio.run(run())


def test_reconcile_clears_low_flag_when_restocked(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        s = await db.insert_supply({"name": "F", "quantity": 1, "low_threshold": 2})
        await db.update_supply(s["id"], low_notified=1)
        await db.update_supply(s["id"], quantity=8)
        row = await supplies.reconcile_low_flag(db, s["id"])
        assert row["low_notified"] == 0

    asyncio.run(run())


def test_is_due_cadence():
    old = "2026-06-01T00:00:00+00:00"
    import datetime as dt
    now = dt.datetime(2026, 6, 20, tzinfo=dt.timezone.utc)
    assert supplies.is_due({"refill_days": 14, "last_refill_at": old}, now) is True
    assert supplies.is_due({"refill_days": 30, "last_refill_at": old}, now) is False
    assert supplies.is_due({"refill_days": None, "last_refill_at": old}, now) is False


def test_reminder_text_covers_reasons():
    s = {"name": "Formula", "quantity": 1, "unit": "cans",
         "low_threshold": 2, "refill_days": 14}
    title, msg = supplies.reminder_text(s, ["low", "due"])
    assert "Formula" in title and "low" in title
    assert "cans" in msg and "refill" in msg.lower()


# --- REST endpoints --------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    monkeypatch.setenv("DEFAULT_TAB", "contractions")
    from app import main
    app = main.create_app(Config.load())
    with TestClient(app) as c:
        yield c


def test_config_endpoint_reports_default_tab(client):
    assert client.get("/api/config").json()["default_tab"] == "contractions"


def test_supply_crud_and_refill_journals(client):
    # create
    r = client.post("/api/supplies", json={
        "category": "formula", "name": "Kirkland", "quantity": 3,
        "low_threshold": 2, "consume_event_type": "feed",
        "consume_event_subtype": "bottle", "consume_amount": 1})
    assert r.status_code == 200
    sid = r.json()["supply"]["id"]

    # a live bottle feed auto-decrements 3 -> 2 and marks low
    client.post("/api/event", json={"event_type": "feed", "event_subtype": "bottle"})
    s = client.get("/api/supplies").json()["supplies"][0]
    assert s["quantity"] == 2 and s["is_low"] is True

    # a BACKFILLED feed must NOT decrement (past event)
    client.post("/api/event", json={"event_type": "feed", "event_subtype": "bottle",
                                    "logged_at": "2026-06-01T10:00:00+00:00"})
    assert client.get("/api/supplies").json()["supplies"][0]["quantity"] == 2

    # manual adjust +5 clears low
    client.post(f"/api/supplies/{sid}/adjust", json={"delta": 5})
    s = client.get("/api/supplies").json()["supplies"][0]
    assert s["quantity"] == 7 and s["is_low"] is False and s["low_notified"] == 0

    # refill logs a supply event into the journal
    before = len(client.get("/api/log").json()["entries"])
    client.post(f"/api/supplies/{sid}/refill", json={"quantity": 8})
    entries = client.get("/api/log").json()["entries"]
    assert len(entries) == before + 1
    assert entries[0]["event_type"] == "supply"

    # delete
    assert client.delete(f"/api/supplies/{sid}").json()["ok"] is True
    assert client.get("/api/supplies").json()["supplies"] == []


def test_checklist_endpoints(client):
    items = client.get("/api/checklist").json()["items"]
    assert len(items) == len(DEFAULT_CHECKLIST)
    cid = items[0]["id"]
    # check it done
    assert client.patch(f"/api/checklist/{cid}", json={"done": True}).json()["item"]["done"] == 1
    # add one
    client.post("/api/checklist", json={"label": "Pump bag"})
    assert any(i["label"] == "Pump bag" for i in client.get("/api/checklist").json()["items"])
    # reset unchecks everything
    client.post("/api/checklist/reset", json={})
    assert all(i["done"] == 0 for i in client.get("/api/checklist").json()["items"])
    # delete
    assert client.delete(f"/api/checklist/{cid}").json()["ok"] is True


def test_contraction_medium_logs(client):
    r = client.post("/api/event", json={"event_type": "contraction",
                                        "event_subtype": "medium"})
    assert r.status_code == 200
    assert r.json()["event"]["event_subtype"] == "medium"
