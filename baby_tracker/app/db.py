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

from . import i18n
from .timefmt import clock, normalize

# Sentinel for partial updates: distinguishes "leave unchanged" from "set NULL".
_UNSET = object()

# Get Ready tab: popular prep suggestions seeded when the table is first
# created (all editable). Catalog keys, so the seeds follow the `language`
# option instead of always arriving in English.
DEFAULT_CHECKLIST = [
    "ready.seed.crib",
    "ready.seed.diaperBag",
    "ready.seed.clothes",
    "ready.seed.bottles",
    "ready.seed.wipes",
    "ready.seed.carSeat",
]


def default_checklist(lang: str = "en", data_dir=None) -> list[str]:
    """The seed labels, translated."""
    return [i18n.t(key, lang, data_dir) for key in DEFAULT_CHECKLIST]

# Editable columns of baby_supplies (order used by insert/row mapping).
SUPPLY_FIELDS = (
    "category", "name", "brand", "type", "quantity", "unit",
    "low_threshold", "refill_days", "last_refill_at",
    "consume_event_type", "consume_event_subtype", "consume_amount",
    "low_notified", "created_at", "updated_at",
)

# Editable columns of baby_reminders (recurring reminder series, SDD-007).
REMINDER_FIELDS = (
    "title", "event_type", "event_subtype", "mode", "interval_min", "at_time",
    "start_at", "stop_at", "enabled", "last_fired_at", "fire_count",
    "created_at", "updated_at",
)

# Backup/restore (issue #5): the per-table column set dumped and reloaded by
# export_all / import_all. `id` is intentionally excluded so a restore reassigns
# fresh primary keys (no sequence surgery, no clashes with existing rows).
EXPORT_TABLES = {
    "baby_events": ("event_type", "event_subtype", "note", "logged_at", "value",
                    "value_unit", "reminder_id"),
    "baby_supplies": SUPPLY_FIELDS,
    "baby_checklist": ("label", "position", "done", "done_at", "updated_at"),
    "baby_summaries": ("text", "provider", "source", "generated_at", "day"),
    "baby_reminders": REMINDER_FIELDS,
}

# Timestamp columns that Postgres stores as timestamptz (need datetime on insert).
_PG_TS_COLS = {"logged_at"}


def _json_safe(v):
    """Coerce a DB value to something JSON-serializable (datetimes -> isoformat)."""
    if isinstance(v, dt.datetime):
        return v.isoformat()
    return v


def _round_value(v):
    """Trim single-precision float noise off a numeric reading on read.

    The Postgres archive stores `value` as `real` (4-byte single precision), so
    100.8 comes back as 100.80000305175781 and, rendered raw, shows 8-9 junk
    digits in the UI/notifications. Readings (temperature, weight, length, head)
    never carry more than 2 real decimals, so rounding there is lossless and
    kills the artifact for every consumer (web, MQTT, title/message, summary)."""
    return round(v, 2) if isinstance(v, float) else v


def _clean_row(d: dict) -> dict:
    """Round a row's numeric `value` in place (see `_round_value`)."""
    if d.get("value") is not None:
        d["value"] = _round_value(d["value"])
    return d


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _fmt_time(iso: str, tz: ZoneInfo, time_format: str = "12h") -> str:
    """Journal timestamp: "7:42 PM, Aug 28" or, under 24h, "19:42, Aug 28".

    Only the clock half follows `time_format` (SDD-006); the date half is the
    same either way."""
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    d = d.astimezone(tz)
    return f"{clock(d, time_format)}, {d.strftime('%b %d')}"


def _is_postgres(url: str | None) -> bool:
    if not url:
        return False
    scheme = urlparse(url).scheme.lower()
    return scheme in ("postgres", "postgresql", "postgresql+asyncpg")


def Database(path=None, timezone: str = "America/New_York", database_url: str | None = None,
             time_format: str = "12h"):
    """Factory returning the right backend.

    Back-compat: existing callers do `Database(cfg.db_path, cfg.timezone)`. When
    `database_url` is a postgres URL we return the Postgres backend instead and
    ignore `path`. `time_format` styles the `time` field on returned rows.
    """
    if _is_postgres(database_url):
        return PostgresDatabase(database_url, timezone, time_format)
    return SqliteDatabase(path, timezone, time_format)


# ---------------------------------------------------------------------------
# SQLite backend (default)
# ---------------------------------------------------------------------------

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS baby_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type    TEXT NOT NULL,
    event_subtype TEXT,
    note          TEXT,
    logged_at     TEXT NOT NULL,
    value         REAL,
    value_unit    TEXT,
    -- The reminder series this row was logged from, when it was (SDD-008).
    -- Only a row carrying it re-anchors that series' countdown, so two medicine
    -- series never reset each other. NULL for every ordinary log.
    reminder_id   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_baby_events_logged_at ON baby_events (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_baby_events_type ON baby_events (event_type);

CREATE TABLE IF NOT EXISTS baby_supplies (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    category              TEXT NOT NULL,
    name                  TEXT NOT NULL,
    brand                 TEXT,
    type                  TEXT,
    quantity              REAL NOT NULL DEFAULT 0,
    unit                  TEXT,
    low_threshold         REAL,
    refill_days           INTEGER,
    last_refill_at        TEXT,
    consume_event_type    TEXT,
    consume_event_subtype TEXT,
    consume_amount        REAL NOT NULL DEFAULT 1,
    low_notified          INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baby_checklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    done       INTEGER NOT NULL DEFAULT 0,
    done_at    TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baby_summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    text         TEXT NOT NULL,
    provider     TEXT,
    source       TEXT,
    generated_at TEXT NOT NULL,
    day          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baby_summaries_day ON baby_summaries (day);

CREATE TABLE IF NOT EXISTS baby_reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    event_type    TEXT,
    event_subtype TEXT,
    mode          TEXT NOT NULL DEFAULT 'interval',
    interval_min  INTEGER,
    at_time       TEXT,
    start_at      TEXT NOT NULL,
    stop_at       TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_fired_at TEXT,
    fire_count    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


class SqliteDatabase:
    def __init__(self, path, timezone: str = "America/New_York", time_format: str = "12h"):
        self.path = str(path)
        self.tz = ZoneInfo(timezone)
        self.time_fmt = normalize(time_format)

    async def init(self, seed: list[str] | None = None) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            # Seed the Get Ready list only when THIS init creates the table.
            # Seeding whenever it was empty put the six defaults back on every
            # restart after a parent had deleted them all (issue #9).
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'baby_checklist'"
            )
            fresh = await cur.fetchone() is None
            await db.executescript(SQLITE_SCHEMA)
            # Migration: add numeric value columns to a pre-existing baby_events
            # (SQLite has no ADD COLUMN IF NOT EXISTS — check PRAGMA first).
            cur = await db.execute("PRAGMA table_info(baby_events)")
            cols = {r[1] for r in await cur.fetchall()}
            if "value" not in cols:
                await db.execute("ALTER TABLE baby_events ADD COLUMN value REAL")
            if "value_unit" not in cols:
                await db.execute("ALTER TABLE baby_events ADD COLUMN value_unit TEXT")
            if "reminder_id" not in cols:
                await db.execute("ALTER TABLE baby_events ADD COLUMN reminder_id INTEGER")
            if fresh:
                now = _now_iso()
                labels = default_checklist() if seed is None else seed
                await db.executemany(
                    "INSERT INTO baby_checklist (label, position, updated_at) "
                    "VALUES (?, ?, ?)",
                    [(label, i, now) for i, label in enumerate(labels)],
                )
            await db.commit()

    async def insert_event(
        self,
        event_type: str,
        event_subtype: str | None = None,
        note: str | None = None,
        logged_at: str | None = None,
        value: float | None = None,
        value_unit: str | None = None,
        reminder_id: int | None = None,
    ) -> int:
        import aiosqlite

        logged_at = logged_at or dt.datetime.now(dt.timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO baby_events (event_type, event_subtype, note, logged_at, "
                "value, value_unit, reminder_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_type, event_subtype or None, note or None, logged_at,
                 value, value_unit or None, reminder_id),
            )
            await db.commit()
            return cur.lastrowid

    async def recent(self, limit: int = 200) -> list[dict]:
        """Most-recent rows first, each with a TZ-formatted `time` field."""
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at, value, value_unit, reminder_id "
                "FROM baby_events ORDER BY logged_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            d = _clean_row(dict(r))
            d["time"] = _fmt_time(d["logged_at"], self.tz, self.time_fmt)
            out.append(d)
        return out

    async def latest_of_type(self, event_type: str) -> dict | None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at, value, value_unit, reminder_id "
                "FROM baby_events WHERE event_type = ? ORDER BY logged_at DESC LIMIT 1",
                (event_type,),
            )
            r = await cur.fetchone()
        return _clean_row(dict(r)) if r else None

    async def metric_series(self, event_type: str, limit: int = 30) -> list[dict]:
        """Last `limit` numeric readings of a type, OLDEST->newest (for trends)."""
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, value, value_unit, logged_at FROM baby_events "
                "WHERE event_type = ? AND value IS NOT NULL "
                "ORDER BY logged_at DESC LIMIT ?",
                (event_type, limit),
            )
            rows = await cur.fetchall()
        out = [_clean_row(dict(r)) for r in rows]
        for d in out:
            d["time"] = _fmt_time(d["logged_at"], self.tz, self.time_fmt)
        out.reverse()
        return out

    async def get_event(self, event_id: int) -> dict | None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, event_type, event_subtype, note, logged_at, value, value_unit, reminder_id "
                "FROM baby_events WHERE id = ?",
                (event_id,),
            )
            r = await cur.fetchone()
        return _clean_row(dict(r)) if r else None

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
                "SELECT id, event_type, event_subtype, note, logged_at, value, value_unit, reminder_id "
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

    # --- supplies ----------------------------------------------------------
    async def list_supplies(self) -> list[dict]:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM baby_supplies ORDER BY category, name"
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_supply(self, sid: int) -> dict | None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM baby_supplies WHERE id = ?", (sid,))
            r = await cur.fetchone()
        return dict(r) if r else None

    async def insert_supply(self, data: dict) -> dict:
        import aiosqlite

        now = _now_iso()
        vals = {
            "category": data.get("category") or "other",
            "name": data.get("name") or "Supply",
            "brand": data.get("brand") or None,
            "type": data.get("type") or None,
            "quantity": float(data.get("quantity") or 0),
            "unit": data.get("unit") or None,
            "low_threshold": _num_or_none(data.get("low_threshold")),
            "refill_days": _int_or_none(data.get("refill_days")),
            "last_refill_at": data.get("last_refill_at") or now,
            "consume_event_type": data.get("consume_event_type") or None,
            "consume_event_subtype": data.get("consume_event_subtype") or None,
            "consume_amount": float(data.get("consume_amount") or 1),
            "low_notified": 0,
            "created_at": now,
            "updated_at": now,
        }
        cols = ", ".join(vals.keys())
        ph = ", ".join("?" for _ in vals)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                f"INSERT INTO baby_supplies ({cols}) VALUES ({ph})", tuple(vals.values())
            )
            await db.commit()
            sid = cur.lastrowid
        return await self.get_supply(sid)

    async def update_supply(self, sid: int, **fields) -> dict | None:
        import aiosqlite

        allowed = {k: v for k, v in fields.items() if k in SUPPLY_FIELDS}
        if not allowed:
            return await self.get_supply(sid)
        allowed["updated_at"] = _now_iso()
        sets = ", ".join(f"{k} = ?" for k in allowed)
        vals = list(allowed.values()) + [sid]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE baby_supplies SET {sets} WHERE id = ?", vals)
            await db.commit()
        return await self.get_supply(sid)

    async def delete_supply(self, sid: int) -> bool:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM baby_supplies WHERE id = ?", (sid,))
            await db.commit()
            return cur.rowcount > 0

    # --- checklist ---------------------------------------------------------
    async def list_checklist(self) -> list[dict]:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM baby_checklist ORDER BY position, id"
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_checklist_item(self, cid: int) -> dict | None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM baby_checklist WHERE id = ?", (cid,))
            r = await cur.fetchone()
        return dict(r) if r else None

    async def insert_checklist(self, label: str, position: int | None = None) -> dict:
        import aiosqlite

        now = _now_iso()
        async with aiosqlite.connect(self.path) as db:
            if position is None:
                cur = await db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM baby_checklist")
                (position,) = await cur.fetchone()
            cur = await db.execute(
                "INSERT INTO baby_checklist (label, position, updated_at) VALUES (?, ?, ?)",
                (label, position, now),
            )
            await db.commit()
            cid = cur.lastrowid
        return await self.get_checklist_item(cid)

    async def update_checklist(self, cid: int, label=_UNSET, done=_UNSET,
                               position=_UNSET) -> dict | None:
        import aiosqlite

        sets, vals = ["updated_at = ?"], [_now_iso()]
        if label is not _UNSET:
            sets.append("label = ?"); vals.append(label)
        if done is not _UNSET:
            sets.append("done = ?"); vals.append(1 if done else 0)
            sets.append("done_at = ?"); vals.append(_now_iso() if done else None)
        if position is not _UNSET:
            sets.append("position = ?"); vals.append(int(position))
        vals.append(cid)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE baby_checklist SET {', '.join(sets)} WHERE id = ?", vals)
            await db.commit()
        return await self.get_checklist_item(cid)

    async def delete_checklist(self, cid: int) -> bool:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM baby_checklist WHERE id = ?", (cid,))
            await db.commit()
            return cur.rowcount > 0

    async def reset_checklist(self) -> None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE baby_checklist SET done = 0, done_at = NULL, updated_at = ?",
                (_now_iso(),),
            )
            await db.commit()

    # --- AI summaries ------------------------------------------------------
    async def insert_summary(self, text: str, provider: str, source: str,
                             day: str) -> dict:
        import aiosqlite

        now = _now_iso()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO baby_summaries (text, provider, source, generated_at, day) "
                "VALUES (?, ?, ?, ?, ?)",
                (text, provider, source, now, day),
            )
            await db.commit()
            sid = cur.lastrowid
        return {"id": sid, "text": text, "provider": provider, "source": source,
                "generated_at": now, "day": day}

    async def latest_summary(self) -> dict | None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM baby_summaries ORDER BY generated_at DESC LIMIT 1"
            )
            r = await cur.fetchone()
        return dict(r) if r else None

    async def count_summaries_today(self, day: str) -> int:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM baby_summaries WHERE day = ?", (day,)
            )
            (n,) = await cur.fetchone()
        return int(n)

    # --- reminder series (SDD-007) -----------------------------------------
    async def list_reminders(self) -> list[dict]:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM baby_reminders ORDER BY enabled DESC, created_at DESC"
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_reminder(self, rid: int) -> dict | None:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM baby_reminders WHERE id = ?", (rid,))
            r = await cur.fetchone()
        return dict(r) if r else None

    async def insert_reminder(self, data: dict) -> dict:
        import aiosqlite

        vals = _reminder_values(data)
        cols = ", ".join(vals.keys())
        ph = ", ".join("?" for _ in vals)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                f"INSERT INTO baby_reminders ({cols}) VALUES ({ph})", tuple(vals.values())
            )
            await db.commit()
            rid = cur.lastrowid
        return await self.get_reminder(rid)

    async def update_reminder(self, rid: int, **fields) -> dict | None:
        import aiosqlite

        allowed = {k: v for k, v in fields.items() if k in REMINDER_FIELDS}
        if not allowed:
            return await self.get_reminder(rid)
        allowed["updated_at"] = _now_iso()
        sets = ", ".join(f"{k} = ?" for k in allowed)
        vals = list(allowed.values()) + [rid]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE baby_reminders SET {sets} WHERE id = ?", vals)
            await db.commit()
        return await self.get_reminder(rid)

    async def delete_reminder(self, rid: int) -> bool:
        import aiosqlite

        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM baby_reminders WHERE id = ?", (rid,))
            await db.commit()
            return cur.rowcount > 0

    # -- Backup / restore (issue #5) -----------------------------------------
    async def export_all(self) -> dict:
        import aiosqlite

        tables = {}
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            for table, cols in EXPORT_TABLES.items():
                collist = ", ".join(cols)
                cur = await db.execute(f"SELECT {collist} FROM {table} ORDER BY id")
                tables[table] = [{c: _json_safe(r[c]) for c in cols}
                                 for r in await cur.fetchall()]
        return {"app": "hms-baby-tracker", "schema": 1,
                "exported_at": _now_iso(), "tables": tables}

    async def import_all(self, tables: dict, replace: bool = True) -> dict:
        import aiosqlite

        counts = {}
        async with aiosqlite.connect(self.path) as db:
            for table, cols in EXPORT_TABLES.items():
                rows = tables.get(table) or []
                if replace:
                    await db.execute(f"DELETE FROM {table}")
                collist = ", ".join(cols)
                placeholders = ", ".join("?" * len(cols))
                for r in rows:
                    await db.execute(
                        f"INSERT INTO {table} ({collist}) VALUES ({placeholders})",
                        [r.get(c) for c in cols],
                    )
                counts[table] = len(rows)
            await db.commit()
        return counts


def _reminder_values(data: dict) -> dict:
    """Column values for a new reminder series, shared by both backends."""
    now = _now_iso()
    mode = data.get("mode") or "interval"
    return {
        "title": (data.get("title") or "Reminder").strip()[:120],
        "event_type": data.get("event_type") or None,
        "event_subtype": data.get("event_subtype") or None,
        "mode": mode if mode in ("interval", "daily") else "interval",
        "interval_min": _int_or_none(data.get("interval_min")),
        "at_time": data.get("at_time") or None,
        "start_at": data.get("start_at") or now,
        "stop_at": data.get("stop_at") or None,
        "enabled": 1 if data.get("enabled", True) else 0,
        "last_fired_at": None,
        "fire_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def _num_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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
    logged_at     timestamptz NOT NULL DEFAULT now(),
    value         real,
    value_unit    text,
    -- See the SQLite schema: the series this row was logged from (SDD-008).
    reminder_id   bigint
);
CREATE INDEX IF NOT EXISTS idx_baby_events_logged_at ON baby_events (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_baby_events_type ON baby_events (event_type);
-- Migration for a pre-existing baby_events archive (additive, safe).
ALTER TABLE baby_events ADD COLUMN IF NOT EXISTS value real;
ALTER TABLE baby_events ADD COLUMN IF NOT EXISTS value_unit text;
ALTER TABLE baby_events ADD COLUMN IF NOT EXISTS reminder_id bigint;

CREATE TABLE IF NOT EXISTS baby_supplies (
    id                    bigserial PRIMARY KEY,
    category              text NOT NULL,
    name                  text NOT NULL,
    brand                 text,
    type                  text,
    quantity              real NOT NULL DEFAULT 0,
    unit                  text,
    low_threshold         real,
    refill_days           integer,
    last_refill_at        text,
    consume_event_type    text,
    consume_event_subtype text,
    consume_amount        real NOT NULL DEFAULT 1,
    low_notified          integer NOT NULL DEFAULT 0,
    created_at            text NOT NULL,
    updated_at            text NOT NULL
);

CREATE TABLE IF NOT EXISTS baby_checklist (
    id         bigserial PRIMARY KEY,
    label      text NOT NULL,
    position   integer NOT NULL DEFAULT 0,
    done       integer NOT NULL DEFAULT 0,
    done_at    text,
    updated_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS baby_summaries (
    id           bigserial PRIMARY KEY,
    text         text NOT NULL,
    provider     text,
    source       text,
    generated_at text NOT NULL,
    day          text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baby_summaries_day ON baby_summaries (day);

CREATE TABLE IF NOT EXISTS baby_reminders (
    id            bigserial PRIMARY KEY,
    title         text NOT NULL,
    event_type    text,
    event_subtype text,
    mode          text NOT NULL DEFAULT 'interval',
    interval_min  integer,
    at_time       text,
    start_at      text NOT NULL,
    stop_at       text,
    enabled       integer NOT NULL DEFAULT 1,
    last_fired_at text,
    fire_count    integer NOT NULL DEFAULT 0,
    created_at    text NOT NULL,
    updated_at    text NOT NULL
);
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
    def __init__(self, database_url: str, timezone: str = "America/New_York",
                 time_format: str = "12h"):
        self.dsn = _normalize_pg_url(database_url)
        self.tz = ZoneInfo(timezone)
        self.time_fmt = normalize(time_format)
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        return self._pool

    async def init(self, seed: list[str] | None = None) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            # Same rule as SQLite: seed only when this init creates the table,
            # never merely because it is empty (issue #9).
            fresh = await con.fetchval("SELECT to_regclass('baby_checklist')") is None
            await con.execute(PG_SCHEMA)
            if fresh:
                now = _now_iso()
                labels = default_checklist() if seed is None else seed
                await con.executemany(
                    "INSERT INTO baby_checklist (label, position, updated_at) "
                    "VALUES ($1, $2, $3)",
                    [(label, i, now) for i, label in enumerate(labels)],
                )

    async def insert_event(
        self,
        event_type: str,
        event_subtype: str | None = None,
        note: str | None = None,
        logged_at: str | None = None,
        value: float | None = None,
        value_unit: str | None = None,
        reminder_id: int | None = None,
    ) -> int:
        when = _parse(logged_at) if logged_at else dt.datetime.now(dt.timezone.utc)
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "INSERT INTO baby_events (event_type, event_subtype, note, logged_at, "
                "value, value_unit, reminder_id) VALUES ($1, $2, $3, $4, $5, $6, $7) "
                "RETURNING id",
                event_type, event_subtype or None, note or None, when,
                value, value_unit or None, reminder_id,
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
        return _clean_row(d)

    async def recent(self, limit: int = 200) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, event_type, event_subtype, note, logged_at, value, value_unit, reminder_id "
                "FROM baby_events ORDER BY logged_at DESC LIMIT $1",
                limit,
            )
        out = []
        for r in rows:
            d = self._row_to_dict(r)
            d["time"] = _fmt_time(d["logged_at"], self.tz, self.time_fmt)
            out.append(d)
        return out

    async def latest_of_type(self, event_type: str) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT id, event_type, event_subtype, note, logged_at, value, value_unit, reminder_id "
                "FROM baby_events WHERE event_type = $1 ORDER BY logged_at DESC LIMIT 1",
                event_type,
            )
        return self._row_to_dict(r) if r else None

    async def metric_series(self, event_type: str, limit: int = 30) -> list[dict]:
        """Last `limit` numeric readings of a type, OLDEST->newest (for trends)."""
        pool = await self._get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, value, value_unit, logged_at FROM baby_events "
                "WHERE event_type = $1 AND value IS NOT NULL "
                "ORDER BY logged_at DESC LIMIT $2",
                event_type, limit,
            )
        out = []
        for r in rows:
            d = self._row_to_dict(r)
            d["time"] = _fmt_time(d["logged_at"], self.tz, self.time_fmt)
            out.append(d)
        out.reverse()
        return out

    async def get_event(self, event_id: int) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT id, event_type, event_subtype, note, logged_at, value, value_unit, reminder_id "
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

    # --- supplies ----------------------------------------------------------
    async def list_supplies(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM baby_supplies ORDER BY category, name")
        return [dict(r) for r in rows]

    async def get_supply(self, sid: int) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow("SELECT * FROM baby_supplies WHERE id = $1", int(sid))
        return dict(r) if r else None

    async def insert_supply(self, data: dict) -> dict:
        now = _now_iso()
        vals = {
            "category": data.get("category") or "other",
            "name": data.get("name") or "Supply",
            "brand": data.get("brand") or None,
            "type": data.get("type") or None,
            "quantity": float(data.get("quantity") or 0),
            "unit": data.get("unit") or None,
            "low_threshold": _num_or_none(data.get("low_threshold")),
            "refill_days": _int_or_none(data.get("refill_days")),
            "last_refill_at": data.get("last_refill_at") or now,
            "consume_event_type": data.get("consume_event_type") or None,
            "consume_event_subtype": data.get("consume_event_subtype") or None,
            "consume_amount": float(data.get("consume_amount") or 1),
            "low_notified": 0,
            "created_at": now,
            "updated_at": now,
        }
        cols = ", ".join(vals.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(vals) + 1))
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"INSERT INTO baby_supplies ({cols}) VALUES ({ph}) RETURNING *",
                *vals.values(),
            )
        return dict(row)

    async def update_supply(self, sid: int, **fields) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in SUPPLY_FIELDS}
        if not allowed:
            return await self.get_supply(sid)
        allowed["updated_at"] = _now_iso()
        keys = list(allowed.keys())
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(keys, start=1))
        vals = list(allowed.values()) + [int(sid)]
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"UPDATE baby_supplies SET {sets} WHERE id = ${len(vals)} RETURNING *", *vals
            )
        return dict(row) if row else None

    async def delete_supply(self, sid: int) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            res = await con.execute("DELETE FROM baby_supplies WHERE id = $1", int(sid))
        try:
            return int(res.split()[-1]) > 0
        except (ValueError, IndexError):
            return False

    # --- checklist ---------------------------------------------------------
    async def list_checklist(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM baby_checklist ORDER BY position, id")
        return [dict(r) for r in rows]

    async def get_checklist_item(self, cid: int) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow("SELECT * FROM baby_checklist WHERE id = $1", int(cid))
        return dict(r) if r else None

    async def insert_checklist(self, label: str, position: int | None = None) -> dict:
        now = _now_iso()
        pool = await self._get_pool()
        async with pool.acquire() as con:
            if position is None:
                position = await con.fetchval(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM baby_checklist"
                )
            row = await con.fetchrow(
                "INSERT INTO baby_checklist (label, position, updated_at) "
                "VALUES ($1, $2, $3) RETURNING *",
                label, int(position), now,
            )
        return dict(row)

    async def update_checklist(self, cid: int, label=_UNSET, done=_UNSET,
                               position=_UNSET) -> dict | None:
        sets, vals = ["updated_at"], [_now_iso()]
        if label is not _UNSET:
            sets.append("label"); vals.append(label)
        if done is not _UNSET:
            sets.append("done"); vals.append(1 if done else 0)
            sets.append("done_at"); vals.append(_now_iso() if done else None)
        if position is not _UNSET:
            sets.append("position"); vals.append(int(position))
        assign = ", ".join(f"{k} = ${i}" for i, k in enumerate(sets, start=1))
        vals.append(int(cid))
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"UPDATE baby_checklist SET {assign} WHERE id = ${len(vals)} RETURNING *", *vals
            )
        return dict(row) if row else None

    async def delete_checklist(self, cid: int) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            res = await con.execute("DELETE FROM baby_checklist WHERE id = $1", int(cid))
        try:
            return int(res.split()[-1]) > 0
        except (ValueError, IndexError):
            return False

    async def reset_checklist(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            await con.execute(
                "UPDATE baby_checklist SET done = 0, done_at = NULL, updated_at = $1",
                _now_iso(),
            )

    # --- AI summaries ------------------------------------------------------
    async def insert_summary(self, text: str, provider: str, source: str,
                             day: str) -> dict:
        now = _now_iso()
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "INSERT INTO baby_summaries (text, provider, source, generated_at, day) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING *",
                text, provider, source, now, day,
            )
        return dict(row)

    async def latest_summary(self) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow(
                "SELECT * FROM baby_summaries ORDER BY generated_at DESC LIMIT 1"
            )
        return dict(r) if r else None

    async def count_summaries_today(self, day: str) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            n = await con.fetchval(
                "SELECT COUNT(*) FROM baby_summaries WHERE day = $1", day
            )
        return int(n)

    # --- reminder series (SDD-007) -----------------------------------------
    async def list_reminders(self) -> list[dict]:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM baby_reminders ORDER BY enabled DESC, created_at DESC"
            )
        return [dict(r) for r in rows]

    async def get_reminder(self, rid: int) -> dict | None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            r = await con.fetchrow("SELECT * FROM baby_reminders WHERE id = $1", int(rid))
        return dict(r) if r else None

    async def insert_reminder(self, data: dict) -> dict:
        vals = _reminder_values(data)
        cols = ", ".join(vals.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(vals) + 1))
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"INSERT INTO baby_reminders ({cols}) VALUES ({ph}) RETURNING *",
                *vals.values(),
            )
        return dict(row)

    async def update_reminder(self, rid: int, **fields) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in REMINDER_FIELDS}
        if not allowed:
            return await self.get_reminder(rid)
        allowed["updated_at"] = _now_iso()
        keys = list(allowed.keys())
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(keys, start=1))
        vals = list(allowed.values()) + [int(rid)]
        pool = await self._get_pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"UPDATE baby_reminders SET {sets} WHERE id = ${len(vals)} RETURNING *", *vals
            )
        return dict(row) if row else None

    async def delete_reminder(self, rid: int) -> bool:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            res = await con.execute("DELETE FROM baby_reminders WHERE id = $1", int(rid))
        try:
            return int(res.split()[-1]) > 0
        except (ValueError, IndexError):
            return False

    async def reset(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            await con.execute("DELETE FROM baby_events")

    # -- Backup / restore (issue #5) -----------------------------------------
    async def export_all(self) -> dict:
        pool = await self._get_pool()
        tables = {}
        async with pool.acquire() as con:
            for table, cols in EXPORT_TABLES.items():
                collist = ", ".join(cols)
                rows = await con.fetch(f"SELECT {collist} FROM {table} ORDER BY id")
                tables[table] = [{c: _json_safe(r[c]) for c in cols} for r in rows]
        return {"app": "hms-baby-tracker", "schema": 1,
                "exported_at": _now_iso(), "tables": tables}

    async def import_all(self, tables: dict, replace: bool = True) -> dict:
        pool = await self._get_pool()
        counts = {}
        async with pool.acquire() as con:
            async with con.transaction():
                for table, cols in EXPORT_TABLES.items():
                    rows = tables.get(table) or []
                    if replace:
                        await con.execute(f"DELETE FROM {table}")
                    collist = ", ".join(cols)
                    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
                    for r in rows:
                        vals = [_parse(r[c]) if (c in _PG_TS_COLS and r.get(c))
                                else r.get(c) for c in cols]
                        await con.execute(
                            f"INSERT INTO {table} ({collist}) VALUES ({placeholders})", *vals)
                    counts[table] = len(rows)
        return counts
