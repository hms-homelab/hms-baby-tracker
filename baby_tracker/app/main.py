"""Baby Tracker — FastAPI app (Ingress UI + REST API + MQTT bridge).

A single funnel, `ingest_and_broadcast`, is shared by the REST API and the MQTT
subscriber so every event path behaves identically: store -> (arm pump/feed) ->
publish MQTT state -> notify.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import assessment, ingest, notify
from .config import Config
from .db import Database
from .mqtt import MqttBridge
from .scheduler import Reminders
from .stats import compute

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("baby")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class EventIn(BaseModel):
    event_type: str
    event_subtype: str | None = None
    note: str | None = None
    logged_at: str | None = None  # ISO8601; backfill a missed event. Omit for now().


class EventPatch(BaseModel):
    # Only the fields actually sent are applied (see model_fields_set). `logged_at`
    # is the headline use: fix the time of an event logged late.
    logged_at: str | None = None
    note: str | None = None
    event_subtype: str | None = None


class NoteIn(BaseModel):
    message: str
    special: bool = False


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.load()
    db = Database(cfg.db_path, cfg.timezone, cfg.database_url)
    mqtt = MqttBridge(cfg, db)
    reminders = Reminders(cfg, mqtt=mqtt, db=db)

    async def ingest_and_broadcast(event_type, event_subtype=None, note=None,
                                   source="api", logged_at=None):
        row = await ingest.create_event(db, cfg, event_type, event_subtype, note, logged_at)
        # Reminders are "X minutes from now" — only arm for live events, never for
        # a backfilled past event (logged_at set).
        if logged_at is None and event_type == "pump":
            reminders.arm_pump(row.get("event_subtype") or "?")
        elif logged_at is None and event_type == "feed":
            reminders.arm_feed(row.get("event_subtype") or "")
        snapshot = compute(await db.recent(), cfg.timezone)
        await mqtt.publish_state(snapshot["stats"])
        # Refresh the device OLED rows + alert flag immediately (don't wait for
        # the 60s poll) when a feed/pump just changed the "ago" math.
        if event_type in ("feed", "pump"):
            await reminders.refresh_display()
        # Fire the stored event on MQTT (`baby/event`) so HA automations can
        # trigger on it and notify phones — for every source (web UI, app REST,
        # or the remote), independent of the add-on's own `notify_targets`.
        await mqtt.publish_event({**row, "source": source})
        await notify.notify(cfg, row["title"], row["message"])
        # Contraction AI assessment (n8n "Contraction AI Assessment" webhook).
        # No-op unless ollama_enabled; runs after the event is stored so the
        # 2h window includes it. Fire-and-forget so a slow LLM never blocks the
        # event response.
        if event_type == "contraction" and cfg.ollama_enabled:
            asyncio.create_task(assessment.maybe_assess(cfg, db, mqtt))
        log.info("event[%s] %s/%s -> #%s", source, event_type,
                 row.get("event_subtype") or "", row["id"])
        return row

    async def rebroadcast():
        """Recompute + republish state and refresh the device OLED after an edit
        or delete. No `baby/event` fire / notify — those are for new events only."""
        snapshot = compute(await db.recent(), cfg.timezone)
        await mqtt.publish_state(snapshot["stats"])
        await reminders.refresh_display()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        await db.init()
        reminders.start()
        mqtt.on_event = ingest_and_broadcast

        async def on_connect():
            # Re-publish retained state + device display on every (re)connect so a
            # broker restart doesn't leave the OLED / HA sensors stale.
            await mqtt.publish_state(compute(await db.recent(), cfg.timezone)["stats"])
            await reminders.refresh_display()

        mqtt.on_connect = on_connect
        task = asyncio.create_task(mqtt.run())
        with contextlib.suppress(Exception):
            await mqtt.publish_state(compute(await db.recent(), cfg.timezone)["stats"])
            await reminders.refresh_display()
        try:
            yield
        finally:
            reminders.shutdown()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Baby Tracker", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/api/log")
    async def get_log():
        return compute(await db.recent(200), cfg.timezone)

    @app.post("/api/event")
    async def post_event(ev: EventIn):
        row = await ingest_and_broadcast(
            ev.event_type, ev.event_subtype, ev.note, "api", ev.logged_at
        )
        return {"ok": True, "event": row}

    @app.patch("/api/event/{event_id}")
    async def patch_event(event_id: int, ev: EventPatch):
        if not await db.get_event(event_id):
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        fields = ev.model_fields_set
        kwargs = {}
        # logged_at is NOT NULL — only apply when a real timestamp is sent.
        if "logged_at" in fields and ev.logged_at:
            kwargs["logged_at"] = ev.logged_at
        if "note" in fields:
            kwargs["note"] = ev.note
        if "event_subtype" in fields:
            kwargs["event_subtype"] = ev.event_subtype
        row = await db.update_event(event_id, **kwargs)
        await rebroadcast()
        log.info("event edited -> #%s %s", event_id, sorted(kwargs))
        return {"ok": True, "event": row}

    @app.delete("/api/event/{event_id}")
    async def delete_event(event_id: int):
        if not await db.delete_event(event_id):
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        await rebroadcast()
        log.info("event deleted -> #%s", event_id)
        return {"ok": True}

    @app.post("/api/note")
    async def post_note(n: NoteIn):
        text = ("⭐ " + n.message) if n.special else n.message
        row = await ingest_and_broadcast("note", None, text, "api")
        return {"ok": True, "event": row}

    @app.post("/api/reset")
    async def post_reset():
        await db.reset()
        await mqtt.publish_state(compute([], cfg.timezone)["stats"])
        return {"ok": True}

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:  # dev convenience before the SPA exists
        @app.get("/")
        async def root():
            return JSONResponse({"app": "baby-tracker", "web": "missing"})

    return app


app = create_app()
