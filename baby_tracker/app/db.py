"""SQLite storage for baby events (aiosqlite).

Mirrors the original PostgreSQL `baby_events` table from db/schema.sql, but
self-contained in /data so the add-on needs no external database.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS baby_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type    TEXT NOT NULL,
    event_subtype TEXT,
    note          TEXT,
    logged_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baby_events_logged_at ON baby_events (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_baby_events_type ON baby_events (event_type);
"""

# Matches the Postgres to_char(... 'HH12:MI AM, Mon DD') used by the n8n log API.
_TIME_FMT = "%I:%M %p, %b %d"


def _fmt_time(iso: str, tz: ZoneInfo) -> str:
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(tz).strftime(_TIME_FMT)


class Database:
    def __init__(self, path, timezone: str = "America/New_York"):
        self.path = str(path)
        self.tz = ZoneInfo(timezone)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def insert_event(
        self,
        event_type: str,
        event_subtype: str | None = None,
        note: str | None = None,
        logged_at: str | None = None,
    ) -> int:
        logged_at = logged_at or dt.datetime.now(dt.timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO baby_events (event_type, event_subtype, note, logged_at) "
                "VALUES (?, ?, ?, ?)",
                (event_type, event_subtype or None, note or None, logged_at),
            )
            await db.commit()
            return cur.lastrowid

    async def recent(self, limit: int = 200) -> list[dict]:
        """Most-recent rows first, each with a TZ-formatted `time` field."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events ORDER BY logged_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["time"] = _fmt_time(d["logged_at"], self.tz)
            out.append(d)
        return out

    async def latest_of_type(self, event_type: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events WHERE event_type = ? ORDER BY logged_at DESC LIMIT 1",
                (event_type,),
            )
            r = await cur.fetchone()
        return dict(r) if r else None

    async def reset(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM baby_events")
            await db.execute("DELETE FROM sqlite_sequence WHERE name='baby_events'")
            await db.commit()
