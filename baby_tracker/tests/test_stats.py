"""Parity tests for the Baby Tracker stats engine (app.stats.compute).

The engine takes rows ordered most-recent-first (logged_at DESC) and returns
{count, stats, entries}. A fixed `now` is used so every assertion is
deterministic. Timezone is America/New_York throughout.
"""
from datetime import datetime, timedelta, timezone

from app.stats import compute

# Fixed reference instant: 2026-06-02 18:00:00 UTC == 14:00 EDT (NY).
NOW = datetime(2026, 6, 2, 18, 0, 0, tzinfo=timezone.utc)
TZ = "America/New_York"


def _ago(minutes: int) -> str:
    """logged_at ISO8601 for `minutes` before the fixed NOW."""
    return (NOW - timedelta(minutes=minutes)).isoformat()


def test_empty():
    out = compute([], TZ, NOW)
    assert out["count"] == 0
    s = out["stats"]
    assert s["feeds_today"] == 0
    assert s["diapers_today"] == 0
    assert s["sleep_min_today"] == 0
    assert s["baths_today"] == 0
    assert s["medicines_today"] == 0
    assert s["tummy_times_today"] == 0
    assert s["pumps_today"] == 0
    assert s["is_sleeping"] is False
    assert s["last_feed_min"] is None
    assert s["last_feed_type"] is None
    assert s["last_diaper_min"] is None
    assert s["last_diaper_type"] is None
    assert s["sleep_total_today"] == "0h 0m"
    assert out["entries"] == []


def test_counts_and_last():
    # Most-recent-first ordering (smallest "minutes ago" first).
    rows = [
        {"id": 8, "event_type": "diaper", "event_subtype": "change",
         "note": None, "logged_at": _ago(43)},
        {"id": 7, "event_type": "feed", "event_subtype": "bottle",
         "note": None, "logged_at": _ago(45)},
        {"id": 6, "event_type": "pump", "event_subtype": "right",
         "note": None, "logged_at": _ago(60)},
        {"id": 5, "event_type": "pump", "event_subtype": "left",
         "note": None, "logged_at": _ago(65)},
        {"id": 4, "event_type": "bath", "event_subtype": None,
         "note": None, "logged_at": _ago(120)},
        {"id": 3, "event_type": "feed", "event_subtype": "breast",
         "note": None, "logged_at": _ago(240)},
    ]
    s = compute(rows, TZ, NOW)["stats"]
    assert s["feeds_today"] == 2
    assert s["diapers_today"] == 1
    assert s["pumps_today"] == 2
    assert s["baths_today"] == 1
    assert s["last_feed_min"] == 45
    assert s["last_feed_type"] == "bottle"
    assert s["last_diaper_min"] == 43
    assert s["last_diaper_type"] == "change"


def test_sleep_pairing():
    # A completed nap today: start 90 min ago, end 30 min ago -> 60 min.
    paired = [
        {"id": 2, "event_type": "sleep", "event_subtype": "end",
         "note": None, "logged_at": _ago(30)},
        {"id": 1, "event_type": "sleep", "event_subtype": "start",
         "note": None, "logged_at": _ago(90)},
    ]
    s = compute(paired, TZ, NOW)["stats"]
    assert s["is_sleeping"] is False
    assert s["sleep_min_today"] == 60
    assert s["sleep_total_today"] == "1h 0m"

    # An open interval: lone start 30 min ago is the most-recent sleep event.
    # The completed nap (60 min) plus the open 30 min interval -> 90 min.
    open_interval = [
        {"id": 3, "event_type": "sleep", "event_subtype": "start",
         "note": None, "logged_at": _ago(30)},
        {"id": 2, "event_type": "sleep", "event_subtype": "end",
         "note": None, "logged_at": _ago(60)},
        {"id": 1, "event_type": "sleep", "event_subtype": "start",
         "note": None, "logged_at": _ago(120)},
    ]
    s2 = compute(open_interval, TZ, NOW)["stats"]
    assert s2["is_sleeping"] is True
    assert s2["sleep_min_today"] == 90  # 60 paired + 30 open (now is fixed)


def test_entries_shape():
    rows = [
        {"id": i, "event_type": "feed", "event_subtype": "bottle",
         "note": f"n{i}", "logged_at": _ago(i), "time": f"t{i}"}
        for i in range(60)
    ]
    out = compute(rows, TZ, NOW)
    entries = out["entries"]
    assert len(entries) == 50  # capped at 50
    # Most-recent first preserved (input order is preserved by the engine).
    assert entries[0]["id"] == 0
    assert entries[-1]["id"] == 49
    for e in entries:
        assert set(e.keys()) == {
            "id", "event_type", "event_subtype", "note", "logged_at", "time"
        }
