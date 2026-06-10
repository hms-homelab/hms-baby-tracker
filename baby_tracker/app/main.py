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

from . import ingest, notify
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


class NoteIn(BaseModel):
    message: str
    special: bool = False


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.load()
    db = Database(cfg.db_path, cfg.timezone)
    reminders = Reminders(cfg)
    mqtt = MqttBridge(cfg)

    async def ingest_and_broadcast(event_type, event_subtype=None, note=None, source="api"):
        row = await ingest.create_event(db, cfg, event_type, event_subtype, note)
        if event_type == "pump":
            reminders.arm_pump(row.get("event_subtype") or "?")
        elif event_type == "feed":
            reminders.arm_feed(row.get("event_subtype") or "")
        snapshot = compute(await db.recent(), cfg.timezone)
        await mqtt.publish_state(snapshot["stats"])
        await notify.notify(cfg, row["title"], row["message"])
        log.info("event[%s] %s/%s -> #%s", source, event_type,
                 row.get("event_subtype") or "", row["id"])
        return row

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        await db.init()
        reminders.start()
        mqtt.on_event = ingest_and_broadcast
        task = asyncio.create_task(mqtt.run())
        with contextlib.suppress(Exception):
            await mqtt.publish_state(compute(await db.recent(), cfg.timezone)["stats"])
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
        row = await ingest_and_broadcast(ev.event_type, ev.event_subtype, ev.note, "api")
        return {"ok": True, "event": row}

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
