"""SDD-006: the 24h clock option.

Covers the shared `app.timefmt.clock` helper and each of the five server-side
surfaces that used to carry its own copy of the AM/PM arithmetic, plus the
promise that an install which never sets the option sees byte-identical output
to what shipped before this change.
"""
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import assessment, summary
from app.config import Config
from app.db import Database, _fmt_time
from app.ingest import format_event
from app.timefmt import clock, normalize

TZ = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


def _at(h, m=0):
    """An aware datetime at local wall-clock h:m (tz-naive arithmetic avoided)."""
    return dt.datetime(2026, 8, 28, h, m, tzinfo=TZ)


# --- the helper ------------------------------------------------------------

# Midnight and noon are the two every hand-rolled 12h formatter gets wrong.
CASES = [
    (0, 0, "12:00 AM", "00:00"),
    (0, 30, "12:30 AM", "00:30"),
    (7, 42, "7:42 AM", "07:42"),      # no leading zero in 12h (issue #2)
    (12, 0, "12:00 PM", "12:00"),
    (13, 5, "1:05 PM", "13:05"),
    (20, 30, "8:30 PM", "20:30"),     # the issue #10 example
    (23, 59, "11:59 PM", "23:59"),
]


@pytest.mark.parametrize("h,m,twelve,twentyfour", CASES)
def test_clock_both_formats(h, m, twelve, twentyfour):
    d = _at(h, m)
    assert clock(d, "12h") == twelve
    assert clock(d, "24h") == twentyfour


def test_clock_defaults_to_12h():
    assert clock(_at(20, 30)) == "8:30 PM"


@pytest.mark.parametrize("bad", ["banana", "", None, "24", "12", "H24"])
def test_unknown_format_falls_back_to_12h(bad):
    """An options.json can hold anything; it must not break the journal."""
    assert clock(_at(20, 30), bad) == "8:30 PM"
    assert normalize(bad) == "12h"


def test_normalize_passes_known_values():
    assert normalize("12h") == "12h"
    assert normalize("24h") == "24h"


# --- db.py: the journal timestamp -----------------------------------------

def test_fmt_time_clock_half_follows_option_date_half_does_not():
    iso = "2026-08-29T00:30:00+00:00"   # 20:30 on Aug 28 in New York
    assert _fmt_time(iso, TZ, "12h") == "8:30 PM, Aug 28"
    assert _fmt_time(iso, TZ, "24h") == "20:30, Aug 28"


def test_fmt_time_defaults_to_12h():
    assert _fmt_time("2026-08-29T00:30:00+00:00", TZ) == "8:30 PM, Aug 28"


def test_fmt_time_passes_through_unparseable_input():
    assert _fmt_time("not-a-date", TZ, "24h") == "not-a-date"


def _db(tmp_path, fmt):
    return Database(str(tmp_path / "baby.sqlite"), "America/New_York", None, fmt)


@pytest.mark.parametrize("fmt,expected", [("12h", "8:30 PM"), ("24h", "20:30")])
def test_recent_and_metric_series_follow_the_option(tmp_path, fmt, expected):
    """The journal (`recent`) and the Health/Growth tabs (`metric_series`)."""
    db = _db(tmp_path, fmt)

    async def run():
        await db.init()
        await db.insert_event("feed", "breast", None, "2026-08-29T00:30:00+00:00")
        await db.insert_event("weight", None, None,
                              "2026-08-29T00:30:00+00:00", 4.2, "kg")
        rows = await db.recent(10)
        series = await db.metric_series("weight", 10)
        return rows, series

    rows, series = asyncio.run(run())
    assert rows[0]["time"].startswith(expected)
    assert series[0]["time"].startswith(expected)


def test_database_defaults_to_12h(tmp_path):
    """Positional back-compat: `Database(path, tz)` is still a 12h install."""
    db = Database(str(tmp_path / "b.sqlite"), "America/New_York")
    assert db.time_fmt == "12h"


def test_postgres_backend_normalizes_the_same_way():
    """Same two lines as SQLite, asserted without standing up Postgres."""
    from app.db import PostgresDatabase
    pg = PostgresDatabase("postgresql://u:p@h/db", "America/New_York", "24h")
    assert pg.time_fmt == "24h"
    assert PostgresDatabase("postgresql://u:p@h/db", "America/New_York").time_fmt == "12h"
    assert PostgresDatabase("postgresql://u:p@h/db", "America/New_York",
                            "nonsense").time_fmt == "12h"


# --- ingest.py: the Home Assistant notification ---------------------------

def test_format_event_message_clock_follows_the_option():
    when = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)   # 20:30 in New York
    _, msg12 = format_event("feed", "breast", None, when, "America/New_York")
    _, msg24 = format_event("feed", "breast", None, when, "America/New_York",
                            time_format="24h")
    assert msg12 == "🍼 Feed (breast) at 8:30 PM"
    assert msg24 == "🍼 Feed (breast) at 20:30"


def test_format_event_keeps_value_and_note_under_24h():
    when = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)
    title, msg = format_event("weight", None, "chunky", when, "America/New_York",
                              4.2, "kg", "24h")
    assert title == "⚖️ Weight 4.2 kg"
    assert msg == "⚖️ Weight 4.2 kg at 20:30\nchunky"


# --- scheduler.py: the reminder text --------------------------------------

@pytest.mark.parametrize("fmt,expected", [("12h", "8:30 PM"), ("24h", "20:30")])
def test_scheduler_now_follows_the_option(monkeypatch, fmt, expected):
    from app.scheduler import Reminders

    cfg = Config(timezone="America/New_York", time_format=fmt)
    rem = Reminders.__new__(Reminders)   # no scheduler/MQTT needed for _now
    rem.cfg = cfg

    fixed = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)

    class _FixedNow(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr("app.scheduler.dt.datetime", _FixedNow)
    _, local = rem._now()
    assert local == expected


# --- summary.py + assessment.py: the MQTT sensor attributes ---------------

@pytest.mark.parametrize("fmt,expected", [("12h", "8:30 PM"), ("24h", "20:30")])
def test_summary_local_time_follows_the_option(fmt, expected):
    cfg = Config(timezone="America/New_York", time_format=fmt)
    now = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)
    assert summary._local_time(cfg, now) == expected


@pytest.mark.parametrize("fmt,expected", [("12h", "8:30 PM"), ("24h", "20:30")])
def test_assessment_local_time_follows_the_option(fmt, expected):
    now = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)
    assert assessment._local_time(TZ, now, fmt) == expected


def test_assessment_leading_zero_is_gone():
    """Was `%I:%M %p` -> "01:05 PM"; the other four had dropped it (issue #2)."""
    now = dt.datetime(2026, 8, 28, 17, 5, tzinfo=UTC)   # 13:05 in New York
    assert assessment._local_time(TZ, now) == "1:05 PM"


def test_build_prompt_local_time_carries_the_format_through():
    now = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)
    rows = [{"logged_at": "2026-08-29T00:30:00+00:00", "event_subtype": "strong"},
            {"logged_at": "2026-08-29T00:25:00+00:00", "event_subtype": "mild"}]
    assert assessment.build_prompt(rows, TZ, now=now)["localTime"] == "8:30 PM"
    assert assessment.build_prompt(rows, TZ, now=now,
                                   time_format="24h")["localTime"] == "20:30"


def test_build_prompt_carries_the_format_on_the_skip_path():
    """Fewer than 2 contractions still stamps a time on the sensor."""
    now = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)
    res = assessment.build_prompt([], TZ, now=now, time_format="24h")
    assert res["skip"] is True and res["localTime"] == "20:30"


# --- /api/config -----------------------------------------------------------

def _client(tmp_path, monkeypatch, fmt):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "")
    if fmt is not None:
        monkeypatch.setenv("TIME_FORMAT", fmt)
    from app import main
    return TestClient(main.create_app(Config.load()))


def test_api_config_reports_time_format(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "24h") as c:
        assert c.get("/api/config").json()["time_format"] == "24h"


def test_api_config_defaults_to_12h(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, None) as c:
        assert c.get("/api/config").json()["time_format"] == "12h"


def test_api_config_sanitizes_a_garbage_option(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "banana") as c:
        assert c.get("/api/config").json()["time_format"] == "12h"


def test_journal_time_is_24h_end_to_end(tmp_path, monkeypatch):
    """POST an event, read it back off /api/log the way the SPA does."""
    with _client(tmp_path, monkeypatch, "24h") as c:
        c.post("/api/event", json={"event_type": "feed", "event_subtype": "breast",
                                   "logged_at": "2026-08-29T00:30:00+00:00"})
        entries = c.get("/api/log").json()["entries"]
        assert entries[0]["time"] == "20:30, Aug 28"


# --- the no-change promise -------------------------------------------------

def test_shipped_default_config_is_12h():
    assert Config().time_format == "12h"


def test_every_surface_defaults_to_the_pre_change_string():
    """One assert per surface, all on the default path. If this file only had
    one regression guard, it would be this one."""
    when = dt.datetime(2026, 8, 29, 0, 30, tzinfo=UTC)
    cfg = Config(timezone="America/New_York")
    assert _fmt_time(when.isoformat(), TZ) == "8:30 PM, Aug 28"
    assert format_event("feed", None, None, when, "America/New_York")[1] == \
        "🍼 Feed at 8:30 PM"
    assert summary._local_time(cfg, when) == "8:30 PM"
    assert assessment._local_time(TZ, when) == "8:30 PM"
