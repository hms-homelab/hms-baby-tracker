"""OLED display + alert publisher — replaces the n8n "Baby Remote Display" flow.

The Baby Remote's OLED is a dumb 3-row renderer: the backend computes the rows
and PUBLISHES them retained to `baby/remote/display` ({"l1","l2","l3"}), plus a
retained `baby/remote/alert` flag ("1"/"0") that the firmware uses to pulse its
LED and pop a "pump due" banner on the rising edge.

n8n ran this every minute off a Postgres poll. We do the same on a 60s job AND
opportunistically right after each event so the device updates instantly. The
math is a faithful port of the n8n "Build Rows" Code node:

  l1 = "Feed <ago> ago"  | "Feed: --"
  l2 = "Pump <ago> ago"  | "Pump: --"
  l3 = "Pump due now"    | "Pump in <eta>" | ""
  alert = "1" when pump is overdue, else "0"

`ago`/`eta` are formatted as "<h>h<m>m" or "<m>m"; <1 min ago renders "now".
The pump-due threshold tracks `cfg.pump_hours` (n8n hard-coded 120 min).
"""
from __future__ import annotations

import datetime as dt
import json
import logging

log = logging.getLogger("baby.display")

DISPLAY_TOPIC = "baby/remote/display"
ALERT_TOPIC = "baby/remote/alert"


def _parse(iso: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _ago_str(iso: str | None, now: dt.datetime) -> str | None:
    if not iso:
        return None
    minutes = int((now - _parse(iso)).total_seconds() // 60)
    if minutes < 1:
        return "now"
    h, m = divmod(minutes, 60)
    return f"{h}h{m}m" if h > 0 else f"{m}m"


def _in_str(minutes: int) -> str:
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60}m"
    return f"{minutes}m"


def build_rows(last_feed_iso: str | None, last_pump_iso: str | None,
               pump_interval_min: int, now: dt.datetime | None = None) -> dict:
    """Return {l1,l2,l3,alert} — the payloads for display + alert topics."""
    now = now or dt.datetime.now(dt.timezone.utc)

    feed_ago = _ago_str(last_feed_iso, now)
    pump_ago = _ago_str(last_pump_iso, now)

    l1 = f"Feed {feed_ago} ago" if feed_ago else "Feed: --"
    l2 = f"Pump {pump_ago} ago" if pump_ago else "Pump: --"

    l3 = ""
    alert = "0"
    if last_pump_iso:
        since = int((now - _parse(last_pump_iso)).total_seconds() // 60)
        due = pump_interval_min - since
        if due <= 0:
            l3 = "Pump due now"
            alert = "1"
        else:
            l3 = "Pump in " + _in_str(due)

    return {"l1": l1, "l2": l2, "l3": l3, "alert": alert}


async def compute_payloads(db, cfg) -> dict:
    """Pull the latest feed/pump from the DB and build the row payloads."""
    last_feed = await db.latest_of_type("feed")
    last_pump = await db.latest_of_type("pump")
    return build_rows(
        last_feed.get("logged_at") if last_feed else None,
        last_pump.get("logged_at") if last_pump else None,
        int(round(cfg.pump_hours * 60)),
    )
