"""Tests for backup/restore export/import (issue #5): a round-trip that
preserves events, supplies and checklist, plus the REST endpoints."""
import asyncio

from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database


def _db(tmp_path):
    return Database(str(tmp_path / "baby.sqlite"), "America/New_York")


def test_export_import_round_trip(tmp_path):
    src = _db(tmp_path / "a")
    (tmp_path / "a").mkdir()
    dst = _db(tmp_path / "b")
    (tmp_path / "b").mkdir()

    async def run():
        await src.init()
        await src.insert_event("feed", "bottle", "note here", "2026-07-01T10:00:00+00:00")
        await src.insert_event("temperature", None, None, "2026-07-01T12:00:00+00:00", 38.5, "°C")
        await src.insert_supply({"category": "diapers", "name": "Size 2", "quantity": 40,
                                 "unit": "ct", "low_threshold": 10, "consume_event_type": "diaper"})
        dump = await src.export_all()
        assert dump["app"] == "hms-baby-tracker"
        assert len(dump["tables"]["baby_events"]) == 2
        assert len(dump["tables"]["baby_supplies"]) == 1

        await dst.init()
        counts = await dst.import_all(dump["tables"], replace=True)
        assert counts["baby_events"] == 2

        events = await dst.recent(50)
        assert len(events) == 2
        temp = [e for e in events if e["event_type"] == "temperature"][0]
        assert temp["value"] == 38.5 and temp["value_unit"] == "°C"
        supplies = await dst.list_supplies()
        assert len(supplies) == 1 and supplies[0]["name"] == "Size 2"
        # Restore is idempotent (replace), not additive.
        await dst.import_all(dump["tables"], replace=True)
        assert len(await dst.recent(50)) == 2

    asyncio.run(run())


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    from app import main
    return TestClient(main.create_app(Config.load()))


def test_export_endpoint_and_import(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        c.post("/api/event", json={"event_type": "diaper", "event_subtype": "pee"})
        dump = c.get("/api/export").json()
        assert dump["app"] == "hms-baby-tracker"
        assert any(e["event_type"] == "diaper" for e in dump["tables"]["baby_events"])

        r = c.post("/api/import", json=dump)
        assert r.status_code == 200 and r.json()["ok"] is True

        bad = c.post("/api/import", json={"nope": 1})
        assert bad.status_code == 400
