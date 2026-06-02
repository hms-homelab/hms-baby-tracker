"""Event ingestion: formatting + sleep-toggle, ported from the n8n flows.

`create_event` is the single funnel used by the REST API and the MQTT
subscriber. It mirrors the n8n "Format Event" node (icons + title/message) and
the `script.baby_sleep_toggle` behaviour.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

ICONS = {
    "feed": "🍼",
    "diaper": "🧷",
    "sleep": "😴",
    "bath": "🛁",
    "medicine": "💊",
    "tummy_time": "🤸",
    "weight": "⚖️",
    "pump": "🤱",
    "note": "📝",
}


def format_event(event_type: str, event_subtype: str | None, note: str | None,
                 when: dt.datetime, timezone: str) -> tuple[str, str]:
    """Return (title, message) exactly like the n8n Format Event node."""
    icon = ICONS.get(event_type, "📝")
    display = event_type.replace("_", " ")
    title = f"{icon} {display[:1].upper()}{display[1:]}"
    if event_subtype:
        title += f" ({event_subtype})"
    ny = when.astimezone(ZoneInfo(timezone))
    h = ny.hour
    ampm = "PM" if h >= 12 else "AM"
    h12 = h % 12 or 12
    time_str = f"{h12}:{ny.minute:02d} {ampm}"
    message = f"{title} at {time_str}"
    if note:
        message += f"\n{note}"
    return title, message


async def resolve_sleep_subtype(db, event_subtype: str | None) -> str:
    """Toggle start<->end based on the latest sleep row (mirrors the HA script)."""
    if event_subtype in ("start", "end"):
        return event_subtype
    latest = await db.latest_of_type("sleep")
    return "end" if (latest and latest.get("event_subtype") == "start") else "start"


async def create_event(db, cfg, event_type: str, event_subtype: str | None = None,
                        note: str | None = None) -> dict:
    """Persist an event; return the stored row + formatted title/message."""
    if event_type == "sleep":
        event_subtype = await resolve_sleep_subtype(db, event_subtype)

    when = dt.datetime.now(dt.timezone.utc)
    logged_at = when.isoformat()
    title, message = format_event(event_type, event_subtype, note, when, cfg.timezone)

    row_id = await db.insert_event(event_type, event_subtype, note, logged_at)
    return {
        "id": row_id,
        "event_type": event_type,
        "event_subtype": event_subtype,
        "note": note,
        "logged_at": logged_at,
        "title": title,
        "message": message,
    }
