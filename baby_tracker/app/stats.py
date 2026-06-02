"""Stats engine — a faithful port of the n8n "Compute Stats" node.

Given the most-recent-first list of baby_events rows, produce the exact same
{count, stats, entries} object the old `GET /webhook/baby-log` returned, so the
UI and journal behave identically.
"""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo


def _js_round(x: float) -> int:
    """Math.round semantics (round half toward +inf), unlike Python's banker's."""
    return math.floor(x + 0.5)


def _parse(iso: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _epoch_ms(iso: str) -> float:
    return _parse(iso).timestamp() * 1000.0


def compute(rows: list[dict], timezone: str = "America/New_York",
            now: dt.datetime | None = None) -> dict:
    """`rows` ordered most-recent-first (logged_at DESC). Mirrors the n8n JS."""
    tz = ZoneInfo(timezone)
    now = now or dt.datetime.now(dt.timezone.utc)
    now_ms = now.timestamp() * 1000.0

    now_ny = now.astimezone(tz)
    today_start = now_ny.replace(hour=0, minute=0, second=0, microsecond=0)

    def in_today(r: dict) -> bool:
        return _parse(r["logged_at"]).astimezone(tz) >= today_start

    today = [r for r in rows if in_today(r)]
    by = lambda t: [r for r in today if r["event_type"] == t]
    feeds_today = by("feed")
    diapers_today = by("diaper")
    sleep_today = by("sleep")
    baths_today = by("bath")
    medicines_today = by("medicine")
    tummy_today = by("tummy_time")

    def first_of(t: str) -> dict | None:
        return next((r for r in rows if r["event_type"] == t), None)

    last_feed = first_of("feed")
    last_feed_min = last_feed_type = None
    if last_feed:
        last_feed_min = _js_round((now_ms - _epoch_ms(last_feed["logged_at"])) / 60000)
        last_feed_type = last_feed.get("event_subtype") or None

    last_diaper = first_of("diaper")
    last_diaper_min = last_diaper_type = None
    if last_diaper:
        last_diaper_min = _js_round((now_ms - _epoch_ms(last_diaper["logged_at"])) / 60000)
        last_diaper_type = last_diaper.get("event_subtype") or None

    latest_sleep = first_of("sleep")
    is_sleeping = bool(latest_sleep and latest_sleep.get("event_subtype") == "start")

    # pair start/end within today (chronological)
    sleep_min = 0.0
    pending_start = None
    for ev in reversed(sleep_today):
        if ev.get("event_subtype") == "start":
            pending_start = _epoch_ms(ev["logged_at"])
        elif ev.get("event_subtype") == "end" and pending_start is not None:
            sleep_min += (_epoch_ms(ev["logged_at"]) - pending_start) / 60000
            pending_start = None
    if is_sleeping and pending_start is not None:
        sleep_min += (now_ms - pending_start) / 60000
    sleep_min = _js_round(sleep_min)

    stats = {
        "last_feed_min": last_feed_min,
        "last_feed_type": last_feed_type,
        "last_diaper_min": last_diaper_min,
        "last_diaper_type": last_diaper_type,
        "feeds_today": len(feeds_today),
        "diapers_today": len(diapers_today),
        "sleep_total_today": f"{sleep_min // 60}h {sleep_min % 60}m",
        "sleep_min_today": sleep_min,
        "is_sleeping": is_sleeping,
        "baths_today": len(baths_today),
        "medicines_today": len(medicines_today),
        "tummy_times_today": len(tummy_today),
        "pumps_today": len(by("pump")),
    }

    entries = [
        {
            "id": r.get("id"),
            "event_type": r["event_type"],
            "event_subtype": r.get("event_subtype"),
            "note": r.get("note"),
            "logged_at": r["logged_at"],
            "time": r.get("time"),
        }
        for r in rows[:50]
    ]

    return {"count": len(rows), "stats": stats, "entries": entries}
