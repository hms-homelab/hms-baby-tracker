"""Tests for SDD-002 Phase 2: numeric value columns (migration + round-trip),
metric_series, the /api/growth + /api/config(fever) endpoints, and value-bearing
events (temperature/weight). SQLite backend + REST against a temp database.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database
from app.ingest import format_event
import datetime as dt


def _db(tmp_path):
    return Database(str(tmp_path / "baby.sqlite"), "America/New_York")


def test_value_columns_roundtrip_and_migration(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        # init is idempotent (migration guarded) — run twice
        await db.init()
        eid = await db.insert_event("weight", None, None,
                                    "2026-07-01T09:00:00+00:00", 4.2, "kg")
        row = await db.get_event(eid)
        assert row["value"] == 4.2 and row["value_unit"] == "kg"
        # recent() carries the value through
        rec = await db.recent()
        assert rec[0]["value"] == 4.2 and rec[0]["value_unit"] == "kg"

    asyncio.run(run())


def test_metric_series_oldest_first_only_valued(tmp_path):
    db = _db(tmp_path)

    async def run():
        await db.init()
        await db.insert_event("weight", None, None, "2026-07-01T09:00:00+00:00", 4.2, "kg")
        await db.insert_event("weight", None, None, "2026-07-03T09:00:00+00:00", 4.4, "kg")
        # a weight row with no value must be excluded from the series
        await db.insert_event("weight", None, "no scale", "2026-07-02T09:00:00+00:00")
        series = await db.metric_series("weight")
        assert [r["value"] for r in series] == [4.2, 4.4]  # oldest -> newest
        assert all("time" in r for r in series)
        assert await db.metric_series("length") == []

    asyncio.run(run())


def test_format_event_includes_value():
    when = dt.datetime(2026, 7, 1, 14, 5, tzinfo=dt.timezone.utc)
    title, msg = format_event("weight", None, None, when, "UTC", 4.2, "kg")
    assert "4.2 kg" in title and "4.2 kg" in msg


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    monkeypatch.setenv("FEVER_THRESHOLD_C", "38.0")
    from app import main
    app = main.create_app(Config.load())
    with TestClient(app) as c:
        yield c


def test_config_reports_fever_threshold(client):
    c = client.get("/api/config").json()
    assert c["fever_threshold_c"] == 38.0
    assert c["measurement_system"] == "imperial"  # shipped default


def test_weight_lb_value_roundtrip(client):
    # 10 lb 4 oz -> 10.25 lb stored as a single decimal value
    r = client.post("/api/event", json={"event_type": "weight",
                                        "value": 10.25, "value_unit": "lb"})
    ev = r.json()["event"]
    assert ev["value"] == 10.25 and ev["value_unit"] == "lb"
    assert "10.25 lb" in ev["title"]


def test_temperature_event_carries_value_into_journal(client):
    r = client.post("/api/event", json={"event_type": "temperature",
                                        "value": 38.5, "value_unit": "°C"})
    assert r.status_code == 200
    ev = r.json()["event"]
    assert ev["value"] == 38.5 and ev["value_unit"] == "°C"
    entry = client.get("/api/log").json()["entries"][0]
    assert entry["event_type"] == "temperature" and entry["value"] == 38.5


def test_contractions_today_in_stats(client):
    client.post("/api/event", json={"event_type": "contraction", "event_subtype": "mild"})
    client.post("/api/event", json={"event_type": "contraction", "event_subtype": "medium"})
    stats = client.get("/api/log").json()["stats"]
    assert stats["contractions_today"] == 2


def test_publish_alert_payload():
    import json as _json
    from app.mqtt import MqttBridge, ALERT_TOPIC

    bridge = MqttBridge(Config())
    captured = {}

    class _Stub:
        async def publish(self, topic, payload, qos=0, retain=False):
            captured["topic"] = topic
            captured["payload"] = payload
            captured["retain"] = retain

    bridge._client = _Stub()
    asyncio.run(bridge.publish_alert("fever", "🌡️ Fever", "39 °C", {"value": 39}))
    assert captured["topic"] == ALERT_TOPIC and captured["retain"] is False
    d = _json.loads(captured["payload"])
    assert d["kind"] == "fever" and d["value"] == 39 and d["title"] == "🌡️ Fever"


def test_growth_endpoint(client):
    client.post("/api/event", json={"event_type": "weight", "value": 4.2, "value_unit": "kg",
                                    "logged_at": "2026-07-01T09:00:00+00:00"})
    client.post("/api/event", json={"event_type": "weight", "value": 4.5, "value_unit": "kg"})
    client.post("/api/event", json={"event_type": "head_circumference", "value": 38, "value_unit": "cm"})
    g = client.get("/api/growth").json()
    assert [r["value"] for r in g["weight"]] == [4.2, 4.5]
    assert len(g["head_circumference"]) == 1
    assert g["length"] == []
