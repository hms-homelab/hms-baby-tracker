"""Event storage for baby events, with a pluggable backend.

The public `Database` API (init / insert_event / recent / latest_of_type /
reset / history) is identical regardless of backend so callers in ingest.py,
stats.py and mqtt.py never change. The driver is chosen from a `DATABASE_URL`:

  * sqlite (DEFAULT, the parent setup) — aiosqlite, a self-contained file in
    /data so the add-on needs no external database.
  * postgresql:// — asyncpg, pointed at an existing `baby_events` table (the
    power-user setup; the table and its real archive already exist there).

The events table is standardized on `baby_events` with columns
(id, event_type, event_subtype, note, logged_at) on both backends.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

# Matches the Postgres to_char(... 'HH12:MI AM, Mon DD') used by the n8n log API.
_TIME_FMT = "%I:%M %p, %b %d"

# Sentinel for partial updates: distinguishes "leave unchanged" from "set NULL".
_UNSET = object()


def _fmt_time(iso: str, tz: ZoneInfo) -> str:
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(tz).strftime(_TIME_FMT)


def _is_postgres(url: str | None) -> bool:
    if not url:
        return False
    scheme = urlparse(url).scheme.lower()
    return scheme in ("postgres", "postgresql", "postgresql+asyncpg")


def Database(path=None, timezone: str = "America/New_York", database_url: str | None = None):
    """Factory returning the right backend.

    Back-compat: existing callers do `Database(cfg.db_path, cfg.timezone)`. When
    `database_url` is a postgres URL we return the Postgres backend instead and
    ignore `path`.
    """
    if _is_postgres(database_url):
        return PostgresDatabase(database_url, timezone)
    return SqliteDatabase(path, timezone)


# ---------------------------------------------------------------------------
# SQLite backend (default)
# ---------------------------------------------------------------------------

SQLITE_SCHEMA = """
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


class SqliteDatabase:
    def __init__(self, path, timezone: str = "America/New_York"):
        self.path = str(path)
        self.tz = ZoneInfo(timezone)

    async def init(self) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SQLITE_SCHEMA)
            await db.commit()

    async def insert_event(
        self,
        event_type: str,
        event_subtype: str | None = None,
        note: str | None = None,
        logged_at: str | None = None,
    ) -> int:
        import aiosqlite

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
        import aiosqlite

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
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events WHERE event_type = ? ORDER BY logged_at DESC LIMIT 1",
                (event_type,),
            )
            r = await cur.fetchone()
        return dict(r) if r else None

    async def get_event(self, event_id: int) -> dict | None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events WHERE id = ?",
                (event_id,),
            )
            r = await cur.fetchone()
        return dict(r) if r else None

    async def update_event(self, event_id: int, logged_at=_UNSET, note=_UNSET,
                           event_subtype=_UNSET) -> dict | None:
        """Partial update; fields left as _UNSET are untouched. Returns the row."""
        import aiosqlite

        sets, vals = [], []
        if logged_at is not _UNSET:
            sets.append("logged_at = ?"); vals.append(logged_at)
        if note is not _UNSET:
            sets.append("note = ?"); vals.append(note or None)
        if event_subtype is not _UNSET:
            sets.append("event_subtype = ?"); vals.append(event_subtype or None)
        if sets:
            vals.append(event_id)
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    f"UPDATE baby_events SET {', '.join(sets)} WHERE id = ?", vals
                )
                await db.commit()
        return await self.get_event(event_id)

    async def delete_event(self, event_id: int) -> bool:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM baby_events WHERE id = ?", (event_id,))
            await db.commit()
            return cur.rowcount > 0

    async def history(self, since: int = 0) -> list[dict]:
        """All events ASC for MQTT replay: {id, ts(epoch s), event_type,
        event_subtype, note}. `since` (unix seconds) filters logged_at > since;
        since<=0 means everything."""
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events ORDER BY logged_at ASC"
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            ts = int(_parse(d["logged_at"]).timestamp())
            if since and ts <= since:
                continue
            out.append({
                "id": d["id"],
                "ts": ts,
                "event_type": d["event_type"],
                "event_subtype": d["event_subtype"],
                "note": d["note"],
            })
        return out

    async def reset(self) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM baby_events")
            await db.execute("DELETE FROM sqlite_sequence WHERE name='baby_events'")
            await db.commit()


# ---------------------------------------------------------------------------
# Postgres backend (asyncpg)
# ---------------------------------------------------------------------------

# The real archive already exists with these columns; CREATE IF NOT EXISTS is a
# safety net for a fresh DB and must never DROP/recreate the user's data.
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS baby_events (
    id            bigserial PRIMARY KEY,
    event_type    text NOT NULL,
    event_subtype text,
    note          text,
    logged_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_baby_events_logged_at ON baby_events (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_baby_events_type ON baby_events (event_type);
"""


def _parse(iso: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _normalize_pg_url(url: str) -> str:
    # asyncpg accepts postgres:// and postgresql:// but not the SQLAlchemy-style
    # postgresql+asyncpg:// — strip the driver tag if present.
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


class PostgresDatabase:
    def __init__(self, database_url: str, timezone: str = "America/New_York"):
        self.dsn = _normalize_pg_url(database_url)
        self.tz = ZoneInfo(timezone)
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        return self._pool

    async def init(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            await con.execute(PG_SCHEMA)

    async def insert_event(
        self,
        event_type: str,
        event_subtype: str | None = None,
        note: str | None = None,
        logged_at: str | None = None,
    ) -> int:
        when = _parse(logged_at) if logged_at else dt.datetime.now(dt.timezone.utc)
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "INSERT INTO baby_events (event_type, event_subtype, note, logged_at) "
                "VALUES ($1, $2, $3, $4) RETURNING id",
                event_type, event_subtype or None, note or None, when,
            )
        return int(row["id"])

    @staticmethod
    def _row_to_dict(r) -> dict:
        d = dict(r)
        la = d.get("logged_at")
        if isinstance(la, dt.datetime):
            if la.tzinfo is None:
                la = la.replace(tzinfo=dt.timezone.utc)
            d["logged_at"] = la.isoformat()
        return d

    async def recent(self, limit: int = 200) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events ORDER BY logged_at DESC LIMIT $1",
                limit,
            )
        out = []
        for r in rows:
            d = self._row_to_dict(r)
            d["time"] = _fmt_time(d["logged_at"], self.tz)
            out.append(d)
        return out

    async def latest_of_type(self, event_type: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events WHERE event_type = $1 ORDER BY logged_at DESC LIMIT 1",
                event_type,
            )
        return self._row_to_dict(r) if r else None

    async def get_event(self, event_id: int) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT id, event_type, event_subtype, note, logged_at "
                "FROM baby_events WHERE id = $1",
                int(event_id),
            )
        return self._row_to_dict(r) if r else None

    async def update_event(self, event_id: int, logged_at=_UNSET, note=_UNSET,
                           event_subtype=_UNSET) -> dict | None:
        """Partial update; fields left as _UNSET are untouched. Returns the row."""
        sets, vals = [], []
        if logged_at is not _UNSET:
            vals.append(_parse(logged_at)); sets.append(f"logged_at = ${len(vals)}")
        if note is not _UNSET:
            vals.append(note or None); sets.append(f"note = ${len(vals)}")
        if event_subtype is not _UNSET:
            vals.append(event_subtype or None); sets.append(f"event_subtype = ${len(vals)}")
        if sets:
            vals.append(int(event_id))
            sql = f"UPDATE baby_events SET {', '.join(sets)} WHERE id = ${len(vals)}"
            pool = await self._get_pool()
            async with pool.acquire() as con:
                await con.execute(sql, *vals)
        return await self.get_event(event_id)

    async def delete_event(self, event_id: int) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            res = await con.execute("DELETE FROM baby_events WHERE id = $1", int(event_id))
        # asyncpg returns a command tag like "DELETE 1".
        try:
            return int(res.split()[-1]) > 0
        except (ValueError, IndexError):
            return False

    async def history(self, since: int = 0) -> list[dict]:
        """All events ASC for MQTT replay, matching the n8n query plus note.
        `since` (unix seconds) filters EXTRACT(EPOCH FROM logged_at) > since."""
        pool = await self._get_pool()
        sql = (
            "SELECT id, event_type, event_subtype, note, "
            "EXTRACT(EPOCH FROM logged_at)::bigint AS ts "
            "FROM baby_events {where} ORDER BY logged_at ASC"
        )
        async with pool.acquire() as con:
            if since and since > 0:
                rows = await con.fetch(
                    sql.format(where="WHERE EXTRACT(EPOCH FROM logged_at) > $1"), since
                )
            else:
                rows = await con.fetch(sql.format(where=""))
        return [
            {
                "id": int(r["id"]),
                "ts": int(r["ts"]),
                "event_type": r["event_type"],
                "event_subtype": r["event_subtype"],
                "note": r["note"],
            }
            for r in rows
        ]

    async def reset(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            await con.execute("DELETE FROM baby_events")
