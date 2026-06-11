"""Parity tests for the device OLED row builder (app.display.build_rows).

Mirrors the n8n "Baby Remote Display" Build Rows Code node: l1/l2 are the
"<x> ago" strings for the last feed/pump, l3 is the pump ETA / due text, and
`alert` flips to "1" once the pump interval has elapsed. A fixed `now` keeps the
assertions deterministic.
"""
from datetime import datetime, timedelta, timezone

from app.display import build_rows

NOW = datetime(2026, 6, 11, 18, 0, 0, tzinfo=timezone.utc)


def _ago(minutes: int) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def test_empty():
    out = build_rows(None, None, 120, NOW)
    assert out == {"l1": "Feed: --", "l2": "Pump: --", "l3": "", "alert": "0"}


def test_pump_due():
    out = build_rows(_ago(95), _ago(130), 120, NOW)
    assert out["l1"] == "Feed 1h35m ago"
    assert out["l2"] == "Pump 2h10m ago"
    assert out["l3"] == "Pump due now"
    assert out["alert"] == "1"


def test_pump_eta():
    out = build_rows(_ago(95), _ago(40), 120, NOW)
    assert out["l3"] == "Pump in 1h20m"
    assert out["alert"] == "0"


def test_eta_under_hour():
    out = build_rows(None, _ago(80), 120, NOW)
    assert out["l3"] == "Pump in 40m"
    assert out["alert"] == "0"


def test_under_one_minute_reads_now():
    # n8n quirk: agoStr returns 'now' for <1 min, yielding "Feed now ago".
    out = build_rows(_ago(0), None, 120, NOW)
    assert out["l1"] == "Feed now ago"
