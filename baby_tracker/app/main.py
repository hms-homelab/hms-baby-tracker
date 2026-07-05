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

from . import assessment, ingest, notify, supplies
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


# Fields a client may set on a supply (create + patch); server-managed columns
# (low_notified/created_at/updated_at/last_refill_at) are excluded.
_SUPPLY_EDITABLE = (
    "category", "name", "brand", "type", "quantity", "unit", "low_threshold",
    "refill_days", "consume_event_type", "consume_event_subtype", "consume_amount",
)


class SupplyIn(BaseModel):
    category: str = "other"
    name: str
    brand: str | None = None
    type: str | None = None
    quantity: float = 0
    unit: str | None = None
    low_threshold: float | None = None
    refill_days: int | None = None
    consume_event_type: str | None = None
    consume_event_subtype: str | None = None
    consume_amount: float = 1


class SupplyPatch(BaseModel):
    category: str | None = None
    name: str | None = None
    brand: str | None = None
    type: str | None = None
    quantity: float | None = None
    unit: str | None = None
    low_threshold: float | None = None
    refill_days: int | None = None
    consume_event_type: str | None = None
    consume_event_subtype: str | None = None
    consume_amount: float | None = None


class AdjustIn(BaseModel):
    delta: float | None = None  # relative change (+/-)
    set: float | None = None    # absolute value


class RefillIn(BaseModel):
    quantity: float | None = None  # new stock level; omit to keep current


class ChecklistIn(BaseModel):
    label: str


class ChecklistPatch(BaseModel):
    label: str | None = None
    done: bool | None = None
    position: int | None = None


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
        # Auto-decrement supplies whose consume rule matches this LIVE event
        # (backfilled past events don't consume — SDD-002 §4.1). Fire a one-shot
        # low reminder for any item that just crossed its threshold.
        if logged_at is None and event_type != "supply":
            for s in await supplies.apply_consumption(db, event_type, row.get("event_subtype")):
                await reminders.fire_supply_reminder(s, ["low"])
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

    # --- UI config (which tab to open on, etc.) ----------------------------
    @app.get("/api/config")
    async def get_config():
        valid = {"get_ready", "baby", "contractions", "health", "growth", "supplies"}
        tab = cfg.default_tab if cfg.default_tab in valid else "baby"
        return {"default_tab": tab}

    # --- supplies ----------------------------------------------------------
    @app.get("/api/supplies")
    async def list_supplies():
        return {"supplies": supplies.annotate_list(await db.list_supplies())}

    @app.post("/api/supplies")
    async def create_supply(s: SupplyIn):
        row = await db.insert_supply(s.model_dump())
        return {"ok": True, "supply": supplies.annotate(row)}

    @app.patch("/api/supplies/{sid}")
    async def patch_supply(sid: int, s: SupplyPatch):
        if not await db.get_supply(sid):
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        fields = {k: getattr(s, k) for k in s.model_fields_set if k in _SUPPLY_EDITABLE}
        row = await db.update_supply(sid, **fields)
        if "quantity" in fields or "low_threshold" in fields:
            row = await supplies.reconcile_low_flag(db, sid)
        return {"ok": True, "supply": supplies.annotate(row)}

    @app.post("/api/supplies/{sid}/adjust")
    async def adjust_supply(sid: int, a: AdjustIn):
        cur = await db.get_supply(sid)
        if not cur:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        if a.set is not None:
            new_qty = max(0.0, a.set)
        else:
            new_qty = max(0.0, (cur.get("quantity") or 0) + (a.delta or 0))
        await db.update_supply(sid, quantity=new_qty)
        row = await supplies.reconcile_low_flag(db, sid)
        return {"ok": True, "supply": supplies.annotate(row)}

    @app.post("/api/supplies/{sid}/refill")
    async def refill_supply(sid: int, r: RefillIn):
        cur = await db.get_supply(sid)
        if not cur:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        import datetime as _dt
        kwargs = {"last_refill_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                  "low_notified": 0}
        if r.quantity is not None:
            kwargs["quantity"] = max(0.0, r.quantity)
        row = await db.update_supply(sid, **kwargs)
        # Log the refill to the shared journal (also fires baby/event).
        await ingest_and_broadcast("supply", cur.get("category"),
                                   f"Refilled {cur.get('name')}", "api")
        return {"ok": True, "supply": supplies.annotate(row)}

    @app.delete("/api/supplies/{sid}")
    async def remove_supply(sid: int):
        if not await db.delete_supply(sid):
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return {"ok": True}

    # --- Get Ready checklist ----------------------------------------------
    @app.get("/api/checklist")
    async def get_checklist():
        return {"items": await db.list_checklist()}

    @app.post("/api/checklist")
    async def add_checklist(c: ChecklistIn):
        row = await db.insert_checklist(c.label)
        return {"ok": True, "item": row}

    @app.patch("/api/checklist/{cid}")
    async def patch_checklist(cid: int, c: ChecklistPatch):
        if not await db.get_checklist_item(cid):
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        kwargs = {}
        fields = c.model_fields_set
        if "label" in fields and c.label is not None:
            kwargs["label"] = c.label
        if "done" in fields and c.done is not None:
            kwargs["done"] = c.done
        if "position" in fields and c.position is not None:
            kwargs["position"] = c.position
        row = await db.update_checklist(cid, **kwargs)
        return {"ok": True, "item": row}

    @app.delete("/api/checklist/{cid}")
    async def remove_checklist(cid: int):
        if not await db.delete_checklist(cid):
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return {"ok": True}

    @app.post("/api/checklist/reset")
    async def reset_checklist():
        await db.reset_checklist()
        return {"ok": True}

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:  # dev convenience before the SPA exists
        @app.get("/")
        async def root():
            return JSONResponse({"app": "baby-tracker", "web": "missing"})

    return app


app = create_app()
