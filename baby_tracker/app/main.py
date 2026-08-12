"""Baby Tracker — FastAPI app (Ingress UI + REST API + MQTT bridge).

A single funnel, `ingest_and_broadcast`, is shared by the REST API and the MQTT
subscriber so every event path behaves identically: store -> (arm pump/feed) ->
publish MQTT state/alerts. Phone notifications are MQTT-based (HA automations
reacting to `baby/event`/`baby/alert`), not a built-in Supervisor-proxy push
(removed, see CHANGELOG - the Supervisor token this needed is unreliable).
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import assessment, i18n, ingest, llm, summary, supplies
from .config import Config
from .db import Database, EXPORT_TABLES
from .mqtt import MqttBridge
from .scheduler import Reminders
from .stats import compute

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("baby")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class NoCacheStaticFiles(StaticFiles):
    """Serve the SPA with `Cache-Control: no-cache` so the browser revalidates
    every asset. Without this, an updated add-on keeps serving the old cached
    app.js/index.html until the browser cache expires — users see the stale UI
    after an update until they manually clear the cache. `no-cache` still allows
    efficient 304s via ETag, so it's cheap.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class EventIn(BaseModel):
    event_type: str
    event_subtype: str | None = None
    note: str | None = None
    logged_at: str | None = None  # ISO8601; backfill a missed event. Omit for now().
    value: float | None = None    # numeric reading (temperature/weight/length/…)
    value_unit: str | None = None


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

    async def state_stats() -> dict:
        """Event stats (baby/state) enriched with cross-tab roll-up counts so the
        new HA discovery sensors (contractions, get-ready, low supplies) work."""
        stats = compute(await db.recent(200), cfg.timezone)["stats"]
        items = await db.list_checklist()
        sups = supplies.annotate_list(await db.list_supplies())
        stats["checklist_done"] = sum(1 for i in items if i["done"])
        stats["checklist_total"] = len(items)
        stats["supplies_low"] = sum(1 for s in sups if s["is_low"])
        stats["supplies_due"] = sum(1 for s in sups if s["is_due"])
        return stats

    async def publish_state_now() -> None:
        await mqtt.publish_state(await state_stats())

    _token = {"v": None}

    def install_token() -> str:
        """Stable random per-install id (for hosted-summary rate-limiting). Not
        PII; minted once and cached in /data/install_token."""
        if _token["v"]:
            return _token["v"]
        tok = None
        p = cfg.data_dir / "install_token"
        try:
            cfg.data_dir.mkdir(parents=True, exist_ok=True)
            tok = p.read_text().strip() if p.exists() else None
        except OSError:
            pass
        if not tok:
            tok = uuid.uuid4().hex
            with contextlib.suppress(OSError):
                p.write_text(tok)
        _token["v"] = tok
        return tok

    _slug = {"v": None}

    async def addon_slug() -> str:
        """This add-on's Supervisor slug (for a deep link to its Configuration).
        Empty when running standalone / without a Supervisor token."""
        if _slug["v"] is not None:
            return _slug["v"]
        _slug["v"] = ""
        if cfg.supervisor_token:
            import httpx
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.get("http://supervisor/addons/self/info",
                                    headers={"Authorization": f"Bearer {cfg.supervisor_token}"})
                    if r.status_code < 400:
                        _slug["v"] = (r.json().get("data") or {}).get("slug") or ""
        return _slug["v"]

    async def auto_summary() -> None:
        with contextlib.suppress(summary.CapReached):
            try:
                await summary.generate(db, cfg, mqtt, install_token(), source="auto")
            except Exception as e:  # never let a bad LLM call kill the scheduler
                log.warning("auto summary failed: %s", e)

    async def ingest_and_broadcast(event_type, event_subtype=None, note=None,
                                   source="api", logged_at=None,
                                   value=None, value_unit=None):
        row = await ingest.create_event(db, cfg, event_type, event_subtype, note,
                                        logged_at, value, value_unit)
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
        await publish_state_now()
        # Refresh the device OLED rows + alert flag immediately (don't wait for
        # the 60s poll) when a feed/pump just changed the "ago" math.
        if event_type in ("feed", "pump"):
            await reminders.refresh_display()
        # Fire the stored event on MQTT (`baby/event`) so HA automations can
        # trigger on it and notify phones — for every source (web UI, app REST,
        # or the remote).
        await mqtt.publish_event({**row, "source": source})
        # Server-side fever alert: a LIVE temperature at/above the threshold fires
        # on the unified baby/alert bus (mirrors the UI's fever badge).
        if logged_at is None and event_type == "temperature" and value is not None:
            c = (value - 32) * 5 / 9 if (value_unit and "F" in value_unit) else value
            if c >= cfg.fever_threshold_c:
                ft, fm = "🌡️ Fever", f"Temperature {ingest._fmt_value(value, value_unit)} — at/above the fever threshold."
                await mqtt.publish_alert("fever", ft, fm, {"value": value, "unit": value_unit})
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
        await publish_state_now()
        await reminders.refresh_display()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        await db.init()
        reminders.start()
        # Daily AI summary cron (SDD-003) — only when enabled and scheduled.
        if cfg.summary_enabled and int(cfg.summary_hour) > 0:
            reminders.sched.add_job(
                auto_summary, "cron", hour=int(cfg.summary_hour), minute=0,
                timezone=cfg.timezone, id="summary_auto", replace_existing=True,
            )
        mqtt.on_event = ingest_and_broadcast

        async def on_connect():
            # Re-publish retained state + device display on every (re)connect so a
            # broker restart doesn't leave the OLED / HA sensors stale.
            await publish_state_now()
            await reminders.refresh_display()

        mqtt.on_connect = on_connect
        task = asyncio.create_task(mqtt.run())
        with contextlib.suppress(Exception):
            await publish_state_now()
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
        result = compute(await db.recent(200), cfg.timezone)
        # Cross-table extras for the summary dashboard (checklist progress +
        # supply alerts) — small tables, cheap to include on each poll.
        items = await db.list_checklist()
        sups = supplies.annotate_list(await db.list_supplies())
        result["summary_extras"] = {
            "checklist": {"done": sum(1 for i in items if i["done"]), "total": len(items)},
            "supplies": {
                "low": [s["name"] for s in sups if s["is_low"]],
                "due": [s["name"] for s in sups if s["is_due"]],
            },
        }
        return result

    @app.post("/api/event")
    async def post_event(ev: EventIn):
        row = await ingest_and_broadcast(
            ev.event_type, ev.event_subtype, ev.note, "api", ev.logged_at,
            ev.value, ev.value_unit,
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
        await publish_state_now()
        return {"ok": True}

    # --- backup / restore (issue #5) --------------------------------------
    @app.get("/api/export")
    async def get_export():
        data = await db.export_all()
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        return JSONResponse(
            data,
            headers={"Content-Disposition":
                     f'attachment; filename="baby-tracker-backup-{stamp}.json"'},
        )

    @app.post("/api/import")
    async def post_import(payload: dict = Body(...)):
        tables = payload.get("tables")
        if not isinstance(tables, dict) or not (set(tables) & set(EXPORT_TABLES)):
            return JSONResponse({"ok": False, "error": "not a Baby Tracker backup file"},
                                status_code=400)
        counts = await db.import_all(tables, replace=True)
        await publish_state_now()
        return {"ok": True, "restored": counts}

    # --- UI config (which tab to open on, etc.) ----------------------------
    @app.get("/api/config")
    async def get_config():
        valid = {"get_ready", "baby", "contractions", "health", "growth", "supplies"}
        hidden = list(cfg.hidden_modules or [])
        tab = cfg.default_tab if cfg.default_tab in valid else "baby"
        # Landing on a hidden tab would open the app to a blank screen.
        if "tab." + tab in hidden:
            tab = "baby"
        system = cfg.measurement_system if cfg.measurement_system in ("imperial", "metric") else "imperial"
        return {"default_tab": tab, "fever_threshold_c": cfg.fever_threshold_c,
                "measurement_system": system, "summary_enabled": cfg.summary_enabled,
                "timezone": cfg.timezone, "language": cfg.language,
                "hidden_modules": hidden,
                "addon_slug": await addon_slug()}

    # --- AI daily summary (SDD-003) ---------------------------------------
    @app.get("/api/summary")
    async def get_summary():
        day = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")
        used = await db.count_summaries_today(day)
        return {
            "enabled": cfg.summary_enabled,
            "latest": await db.latest_summary(),
            "used_today": used,
            "cap": cfg.summary_daily_cap,
            "can_generate": cfg.summary_enabled and used < cfg.summary_daily_cap,
        }

    @app.post("/api/summary")
    async def post_summary():
        if not cfg.summary_enabled:
            return JSONResponse({"ok": False, "error": "disabled"}, status_code=400)
        try:
            row = await summary.generate(db, cfg, mqtt, install_token(), source="manual")
        except (summary.CapReached, llm.CapError):
            return JSONResponse({"ok": False, "error": "cap"}, status_code=429)
        except llm.ProviderError as e:
            return JSONResponse({"ok": False, "error": "provider", "detail": str(e)[:200]},
                                status_code=502)
        return {"ok": True, "summary": row}

    # --- growth trends (weight / length / head circumference) --------------
    @app.get("/api/growth")
    async def get_growth():
        metrics = ("weight", "length", "head_circumference")
        return {m: await db.metric_series(m, 30) for m in metrics}

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

    # --- Translation editor (SDD-004 §3.9) --------------------------------
    # Overrides live in <data_dir>/i18n/, NOT in the image, so a contributor's
    # edits survive an add-on update. Both the SPA and the device path read the
    # merged catalog, so fixing an overlong OLED string here reaches the remote
    # on its next refresh with no rebuild.

    def _known_keys() -> set[str]:
        return {k for k in i18n.shipped("en") if not k.startswith("_")}

    def _validate(overrides: dict) -> tuple[dict, JSONResponse | None]:
        """Server-side gate. The browser's counter is a convenience; this is
        the guarantee."""
        known = _known_keys()
        clean = {}
        for key, val in (overrides or {}).items():
            if key not in known:
                return {}, JSONResponse(
                    {"ok": False, "error": "unknown_key", "key": key},
                    status_code=400)
            if not isinstance(val, str):
                return {}, JSONResponse(
                    {"ok": False, "error": "not_a_string", "key": key},
                    status_code=400)
            if key.startswith("device."):
                folded = i18n._tidy(i18n.ascii_fold(val))
                if not folded.isascii() or len(folded) > i18n.DEVICE_MAX:
                    return {}, JSONResponse(
                        {"ok": False, "error": "device_too_long", "key": key,
                         "length": len(folded), "max": i18n.DEVICE_MAX},
                        status_code=400)
            clean[key] = val
        return clean, None

    def _override_path(lang: str) -> Path:
        d = i18n.override_dir(cfg.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{lang}.json"

    @app.get("/api/i18n/catalog")
    async def i18n_catalog(lang: str = "en"):
        if lang not in i18n.available():
            return JSONResponse({"ok": False, "error": "unknown_language"}, status_code=400)
        en = i18n.shipped("en")
        ship = i18n.shipped(lang)
        over = i18n.overrides(lang, cfg.data_dir)
        rows = []
        for key in en:
            if key.startswith("_"):
                continue
            rows.append({
                "key": key,
                "en": en[key],
                "shipped": ship.get(key),
                "override": over.get(key),
                "effective": over.get(key, ship.get(key, en[key])),
                "is_device": key.startswith("device."),
                "limit": i18n.DEVICE_MAX if key.startswith("device.") else None,
            })
        # `_`-prefixed notes (the device 21-char warning) are not editable rows,
        # but the client re-emits them so an exported file matches the repo's
        # files exactly.
        ship_en = i18n.shipped("en")
        comments = {k: ship.get(k, ship_en.get(k))
                    for k in ship_en if k.startswith("_")}
        return {"lang": lang, "entry": i18n.entry(lang), "rows": rows,
                "comments": comments, "registry": i18n.registry()}

    @app.put("/api/i18n/{lang}")
    async def i18n_save(lang: str, body: dict = Body(...)):
        if lang not in i18n.available():
            return JSONResponse({"ok": False, "error": "unknown_language"}, status_code=400)
        clean, err = _validate(body.get("overrides") or {})
        if err is not None:
            return err
        path = _override_path(lang)
        if clean:
            path.write_text(json.dumps(clean, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        elif path.exists():
            path.unlink()
        i18n.invalidate()
        with contextlib.suppress(Exception):
            await reminders.refresh_display()
        return {"ok": True, "saved": len(clean)}

    @app.delete("/api/i18n/{lang}")
    async def i18n_revert(lang: str, key: str | None = None):
        if lang not in i18n.available():
            return JSONResponse({"ok": False, "error": "unknown_language"}, status_code=400)
        path = _override_path(lang)
        if key:
            current = i18n.overrides(lang, cfg.data_dir)
            current.pop(key, None)
            if current:
                path.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            elif path.exists():
                path.unlink()
        elif path.exists():
            path.unlink()
        i18n.invalidate()
        with contextlib.suppress(Exception):
            await reminders.refresh_display()
        return {"ok": True}

    @app.get("/api/i18n/{lang}/export")
    async def i18n_export(lang: str):
        """A complete catalog, shaped exactly like the files in web/i18n/, so it
        can be dropped into the repo or attached to an issue as-is."""
        if lang not in i18n.available():
            return JSONResponse({"ok": False, "error": "unknown_language"}, status_code=400)
        data = dict(i18n.shipped(lang) or i18n.shipped("en"))
        data.update(i18n.overrides(lang, cfg.data_dir))
        return JSONResponse(
            data,
            headers={"Content-Disposition": f'attachment; filename="{lang}.json"'},
        )

    if WEB_DIR.is_dir():
        app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:  # dev convenience before the SPA exists
        @app.get("/")
        async def root():
            return JSONResponse({"app": "baby-tracker", "web": "missing"})

    return app


app = create_app()
