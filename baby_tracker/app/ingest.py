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
    "contraction": "⏱️",
    "supply": "🧴",
    "temperature": "🌡️",
    "symptom": "🤒",
    "length": "📏",
    "head_circumference": "🧢",
}


def _fmt_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return ""
    num = str(int(value)) if float(value) == int(value) else str(value)
    return f"{num}{(' ' + unit) if unit else ''}"


def format_event(event_type: str, event_subtype: str | None, note: str | None,
                 when: dt.datetime, timezone: str,
                 value: float | None = None, value_unit: str | None = None) -> tuple[str, str]:
    """Return (title, message) exactly like the n8n Format Event node.

    A numeric `value` (temperature/weight/length/head_circumference) is appended
    to both the title `(4.2 kg)` and, on its own line, the message."""
    icon = ICONS.get(event_type, "📝")
    display = event_type.replace("_", " ")
    title = f"{icon} {display[:1].upper()}{display[1:]}"
    if event_subtype:
        title += f" ({event_subtype})"
    val_str = _fmt_value(value, value_unit)
    if val_str:
        title += f" {val_str}"
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


def _parse(iso: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


async def create_event(db, cfg, event_type: str, event_subtype: str | None = None,
                        note: str | None = None, logged_at: str | None = None,
                        value: float | None = None, value_unit: str | None = None) -> dict:
    """Persist an event; return the stored row + formatted title/message.

    `logged_at` (ISO8601) backfills a missed event at a past time; omit it for
    `now()`. For a backfilled sleep event pass an explicit start/end subtype —
    the start<->end auto-toggle only makes sense for live presses. `value` /
    `value_unit` carry a numeric reading (temperature, weight, length, …).
    """
    if event_type == "sleep":
        event_subtype = await resolve_sleep_subtype(db, event_subtype)

    when = _parse(logged_at) if logged_at else dt.datetime.now(dt.timezone.utc)
    logged_at = when.isoformat()
    title, message = format_event(event_type, event_subtype, note, when, cfg.timezone,
                                  value, value_unit)

    row_id = await db.insert_event(event_type, event_subtype, note, logged_at,
                                   value, value_unit)
    return {
        "id": row_id,
        "event_type": event_type,
        "event_subtype": event_subtype,
        "note": note,
        "logged_at": logged_at,
        "value": value,
        "value_unit": value_unit,
        "title": title,
        "message": message,
    }
