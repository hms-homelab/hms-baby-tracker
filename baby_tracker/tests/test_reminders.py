"""Tests for SDD-007: user-created repeating reminder series.

Covers the DB layer, the trigger math in app/scheduler.py (first fire is one
full interval out, catch-up after an outage, stop dates), the alert payload,
and the REST endpoints.
"""
import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database
from app.scheduler import Reminders, at_time_text, every_text, parse_iso


def _db(tmp_path):
    return Database(str(tmp_path / "baby.sqlite"), "America/New_York")


def _cfg(tmp_path):
    return Config(data_dir=tmp_path)


class FakeMqtt:
    def __init__(self):
        self.alerts = []

    async def publish_alert(self, kind, title, message, extra=None):
        self.alerts.append({"kind": kind, "title": title, "message": message,
                            "extra": extra or {}})


def _iso(minutes_from_now: float) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(minutes=minutes_from_now)).isoformat()


# --- helpers ---------------------------------------------------------------

def test_every_text_humanizes_intervals():
    assert every_text(45) == "45m"
    assert every_text(360) == "6h"
    assert every_text(90) == "1h 30m"
    assert every_text(0) == "1m"          # clamped, never "0m"


def test_at_time_follows_the_time_format_option():
    # SDD-006: every clock the app prints goes through timefmt, this one too.
    assert at_time_text("08:00") == "8:00 AM"
    assert at_time_text("20:30") == "8:30 PM"
    assert at_time_text("08:00", "24h") == "08:00"
    assert at_time_text("nope") == "nope"      # never raises on bad data
    assert at_time_text(None) == ""


def test_parse_iso_is_none_safe_and_utc_anchored():
    assert parse_iso(None) is None
    assert parse_iso("nonsense") is None
    assert parse_iso("2026-09-03T10:00:00").tzinfo is dt.timezone.utc
    assert parse_iso("2026-09-03T10:00:00Z").hour == 10


# --- DB layer --------------------------------------------------------------

def test_reminder_crud(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        row = await db.insert_reminder({
            "title": "Tylenol", "mode": "interval", "interval_min": 360,
            "event_type": "medicine",
        })
        assert row["title"] == "Tylenol"
        assert row["enabled"] == 1 and row["fire_count"] == 0
        assert row["start_at"]                      # defaulted to now

        assert [r["id"] for r in await db.list_reminders()] == [row["id"]]

        upd = await db.update_reminder(row["id"], interval_min=240, enabled=0)
        assert upd["interval_min"] == 240 and upd["enabled"] == 0

        assert await db.delete_reminder(row["id"]) is True
        assert await db.delete_reminder(row["id"]) is False
        assert await db.list_reminders() == []

    asyncio.run(run())


def test_reminders_are_backed_up_and_restored(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        await db.insert_reminder({"title": "Vitamin D", "mode": "daily",
                                  "at_time": "08:00"})
        dump = await db.export_all()
        assert len(dump["tables"]["baby_reminders"]) == 1
        assert dump["tables"]["baby_reminders"][0]["title"] == "Vitamin D"

        await db.import_all(dump["tables"], replace=True)
        rows = await db.list_reminders()
        assert len(rows) == 1 and rows[0]["at_time"] == "08:00"

    asyncio.run(run())


# --- trigger math ----------------------------------------------------------

def _sched(tmp_path, db=None, mqtt=None):
    return Reminders(_cfg(tmp_path), mqtt=mqtt, db=db)


def test_interval_first_fire_is_one_full_interval_out(tmp_path):
    r = _sched(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    trig = r._series_trigger({"mode": "interval", "interval_min": 360,
                              "start_at": now.isoformat()})
    first = trig.start_date
    # ~6h from now, never immediately.
    assert dt.timedelta(hours=5, minutes=59) < (first - now) <= dt.timedelta(hours=6)


def test_interval_catches_up_after_an_outage_without_bursting(tmp_path):
    r = _sched(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    # Series armed 25h ago at every 6h: the next slot is in the future, and only
    # one job is armed (no backlog of the four missed runs).
    trig = r._series_trigger({"mode": "interval", "interval_min": 360,
                              "start_at": (now - dt.timedelta(hours=25)).isoformat()})
    assert trig.start_date > now
    assert trig.start_date - now <= dt.timedelta(hours=6)


def test_daily_mode_builds_a_cron_trigger(tmp_path):
    r = _sched(tmp_path)
    trig = r._series_trigger({"mode": "daily", "at_time": "08:30"})
    assert str(trig.fields[trig.FIELD_NAMES.index("hour")]) == "8"
    assert str(trig.fields[trig.FIELD_NAMES.index("minute")]) == "30"


def test_invalid_or_expired_series_do_not_arm(tmp_path):
    r = _sched(tmp_path)
    assert r._series_trigger({"mode": "interval", "interval_min": 0}) is None
    assert r._series_trigger({"mode": "daily", "at_time": "nope"}) is None
    # stop date already in the past
    assert r._series_trigger({"mode": "interval", "interval_min": 60,
                              "start_at": _iso(0), "stop_at": _iso(-60)}) is None
    # stop date lands before the first fire would
    assert r._series_trigger({"mode": "interval", "interval_min": 360,
                              "start_at": _iso(0), "stop_at": _iso(30)}) is None


# --- firing ----------------------------------------------------------------

def test_fire_publishes_alert_and_records_the_run(tmp_path):
    db = _db(tmp_path)
    mqtt = FakeMqtt()
    r = _sched(tmp_path, db=db, mqtt=mqtt)

    async def run():
        await db.init()
        row = await db.insert_reminder({"title": "Tylenol", "mode": "interval",
                                        "interval_min": 360,
                                        "event_type": "medicine"})
        await r._fire_series(row["id"])
        assert len(mqtt.alerts) == 1
        a = mqtt.alerts[0]
        assert a["kind"] == "reminder"
        assert a["title"] == "⏰ Tylenol"
        assert "every 6h" in a["message"]
        assert a["extra"]["reminder_id"] == row["id"]
        assert a["extra"]["event_type"] == "medicine"

        after = await db.get_reminder(row["id"])
        assert after["fire_count"] == 1 and after["last_fired_at"]

    asyncio.run(run())


def test_fire_is_a_no_op_for_a_paused_or_deleted_series(tmp_path):
    db = _db(tmp_path)
    mqtt = FakeMqtt()
    r = _sched(tmp_path, db=db, mqtt=mqtt)

    async def run():
        await db.init()
        row = await db.insert_reminder({"title": "Paused", "mode": "interval",
                                        "interval_min": 60})
        await db.update_reminder(row["id"], enabled=0)
        await r._fire_series(row["id"])
        await r._fire_series(row["id"] + 999)      # deleted / never existed
        assert mqtt.alerts == []

    asyncio.run(run())


def test_fire_past_the_stop_date_retires_the_series(tmp_path):
    db = _db(tmp_path)
    mqtt = FakeMqtt()
    r = _sched(tmp_path, db=db, mqtt=mqtt)

    async def run():
        await db.init()
        row = await db.insert_reminder({"title": "Antibiotic", "mode": "interval",
                                        "interval_min": 60, "stop_at": _iso(-1)})
        await r._fire_series(row["id"])
        assert mqtt.alerts == []
        assert (await db.get_reminder(row["id"]))["enabled"] == 0

    asyncio.run(run())


def test_daily_fire_message_names_the_time(tmp_path):
    db = _db(tmp_path)
    mqtt = FakeMqtt()
    r = _sched(tmp_path, db=db, mqtt=mqtt)

    async def run():
        await db.init()
        row = await db.insert_reminder({"title": "Vitamin D", "mode": "daily",
                                        "at_time": "08:00"})
        await r._fire_series(row["id"])
        # Rendered through the app's clock, not echoed raw (default is 12h).
        assert "8:00 AM" in mqtt.alerts[0]["message"]

    asyncio.run(run())


# --- REST ------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")  # MqttBridge.run no-ops without a host
    from app import main
    app = main.create_app(Config.load())
    with TestClient(app) as c:
        yield c


def test_create_lists_and_arms_immediately(client):
    r = client.post("/api/reminders", json={"title": "Tylenol", "mode": "interval",
                                            "interval_min": 360,
                                            "event_type": "medicine"})
    assert r.status_code == 200
    rem = r.json()["reminder"]
    assert rem["enabled"] is True
    # The worker started with the request: a concrete next run, ~6h out.
    assert rem["next_run"]
    delta = parse_iso(rem["next_run"]) - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(hours=5, minutes=59) < delta <= dt.timedelta(hours=6)

    listed = client.get("/api/reminders").json()["reminders"]
    assert len(listed) == 1 and listed[0]["title"] == "Tylenol"


def test_create_rejects_bad_input(client):
    assert client.post("/api/reminders", json={"title": "  "}).status_code == 400
    assert client.post("/api/reminders",
                       json={"title": "x", "interval_min": 0}).status_code == 400
    assert client.post("/api/reminders",
                       json={"title": "x", "mode": "daily",
                             "at_time": "25:00"}).status_code == 400
    assert client.post("/api/reminders",
                       json={"title": "x", "mode": "weekly"}).status_code == 400


def test_patch_reschedules_and_pauses(client):
    rid = client.post("/api/reminders", json={"title": "Tylenol",
                                              "interval_min": 360}).json()["reminder"]["id"]

    # Shortening the interval restarts the clock from now, not from creation.
    rem = client.patch(f"/api/reminders/{rid}", json={"interval_min": 60}).json()["reminder"]
    delta = parse_iso(rem["next_run"]) - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(minutes=59) < delta <= dt.timedelta(minutes=60)

    paused = client.patch(f"/api/reminders/{rid}", json={"enabled": False}).json()["reminder"]
    assert paused["enabled"] is False and paused["next_run"] is None

    resumed = client.patch(f"/api/reminders/{rid}", json={"enabled": True}).json()["reminder"]
    assert resumed["enabled"] is True and resumed["next_run"]

    assert client.patch("/api/reminders/9999", json={"enabled": True}).status_code == 404


def test_delete_stops_the_series(client):
    rid = client.post("/api/reminders", json={"title": "Tylenol",
                                              "interval_min": 60}).json()["reminder"]["id"]
    assert client.delete(f"/api/reminders/{rid}").status_code == 200
    assert client.get("/api/reminders").json()["reminders"] == []
    assert client.delete(f"/api/reminders/{rid}").status_code == 404


def test_series_survive_a_restart(tmp_path, monkeypatch):
    """The APScheduler jobs live in memory, so a restart must re-arm from the DB."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    from app import main

    with TestClient(main.create_app(Config.load())) as c:
        rid = c.post("/api/reminders", json={"title": "Tylenol",
                                             "interval_min": 360}).json()["reminder"]["id"]

    # Fresh app instance on the same data dir == an add-on restart.
    with TestClient(main.create_app(Config.load())) as c:
        rows = c.get("/api/reminders").json()["reminders"]
        assert len(rows) == 1 and rows[0]["id"] == rid
        assert rows[0]["next_run"], "series was not re-armed after restart"
