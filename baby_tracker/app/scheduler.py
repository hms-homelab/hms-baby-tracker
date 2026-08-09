"""Reminders + device display — replaces the n8n reminder/display flows.

On each pump event we (re)arm a per-side pump timer; on each feed event we
(re)arm a single feed timer. When a timer fires we send the same reminder text
the n8n flows used. A newer event of the same kind reschedules (replaces) the
job, so only the latest pump/feed fires — any feed resets the feed clock.

Additionally, a 60s recurring job refreshes the Baby Remote's OLED rows + the
pump-due alert flag (the n8n "Baby Remote Display" every-minute flow): it polls
the DB for the last feed/pump and publishes `baby/remote/display` +
`baby/remote/alert`. The feed reminder also pops a transient banner on the
device via `baby/remote/reminder`.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import display, i18n, supplies

log = logging.getLogger("baby.scheduler")


class Reminders:
    def __init__(self, cfg, mqtt=None, db=None):
        self.cfg = cfg
        self.mqtt = mqtt  # MqttBridge, for device display/reminder/alert
        self.db = db      # Database, for the periodic display poll
        self.sched = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        if not self.sched.running:
            self.sched.start()
        # Periodic OLED refresh (mirrors n8n's every-minute Display flow).
        if self.mqtt is not None and self.db is not None:
            self.sched.add_job(
                self.refresh_display, "interval", seconds=60,
                id="display_refresh", replace_existing=True,
            )
        if self.db is not None:
            # Daily supplies sweep: low-stock + refill-due reminders (SDD-002).
            self.sched.add_job(
                self.sweep_supplies, "cron",
                hour=int(self.cfg.supply_reminder_hour), minute=0,
                timezone=self.cfg.timezone,
                id="supply_sweep", replace_existing=True,
            )
            # Optional daily Get Ready checklist reset (0 = off).
            if int(self.cfg.checklist_reset_hour) > 0:
                self.sched.add_job(
                    self.reset_checklist, "cron",
                    hour=int(self.cfg.checklist_reset_hour), minute=0,
                    timezone=self.cfg.timezone,
                    id="checklist_reset", replace_existing=True,
                )

    async def refresh_display(self) -> None:
        """Recompute + publish the device OLED rows and pump-due alert flag."""
        if self.mqtt is None or self.db is None:
            return
        try:
            payloads = await display.compute_payloads(self.db, self.cfg)
            await self.mqtt.publish_display(payloads)
        except Exception as e:  # never let a poll error kill the scheduler
            log.warning("display refresh failed: %s", e)

    async def sweep_supplies(self) -> None:
        """Daily low-stock / refill-due reminder pass."""
        if self.db is None:
            return
        try:
            due = await supplies.sweep_reminders(self.db)
        except Exception as e:
            log.warning("supply sweep failed: %s", e)
            return
        for s in due:
            await self.fire_supply_reminder(s, s.get("reasons", []))

    async def fire_supply_reminder(self, supply: dict, reasons: list) -> None:
        """Deliver one supply reminder over MQTT (shared by the sweep and the
        immediate threshold-cross path in the ingest funnel)."""
        title, message = supplies.reminder_text(supply, reasons)
        if self.mqtt is not None:
            kind = "supply_low" if "low" in reasons else "supply_due"
            tag = {"supply": {k: supply.get(k) for k in ("id", "category", "name")}}
            with contextlib.suppress(Exception):
                await self.mqtt.publish_alert(kind, title, message, tag)
            # Legacy alias (baby/supply/reminder) kept for 2026.4.0 automations.
            with contextlib.suppress(Exception):
                await self.mqtt.publish_supply_reminder(title, message, supply)

    async def reset_checklist(self) -> None:
        if self.db is None:
            return
        with contextlib.suppress(Exception):
            await self.db.reset_checklist()
            log.info("Get Ready checklist auto-reset")

    def shutdown(self) -> None:
        if self.sched.running:
            self.sched.shutdown(wait=False)

    def _now(self) -> tuple[dt.datetime, str]:
        now = dt.datetime.now(dt.timezone.utc)
        local = now.astimezone(ZoneInfo(self.cfg.timezone)).strftime("%-I:%M %p")
        return now, local

    @staticmethod
    def _hrs(h: float):
        return int(h) if h == int(h) else h

    def arm_pump(self, side: str) -> None:
        """Schedule (or reschedule) the reminder for one pump side."""
        side = side or "?"
        now, when = self._now()
        run_at = now + dt.timedelta(hours=self.cfg.pump_hours)
        self.sched.add_job(
            self._fire_pump, "date", run_date=run_at,
            args=[side, when], id=f"pump_{side}", replace_existing=True,
        )
        log.info("armed pump reminder side=%s at %s", side, run_at.isoformat())

    def arm_feed(self, subtype: str) -> None:
        """Schedule (or reschedule) the single feed reminder; any feed resets it."""
        now, when = self._now()
        run_at = now + dt.timedelta(hours=self.cfg.feed_hours)
        self.sched.add_job(
            self._fire_feed, "date", run_date=run_at,
            args=[subtype or "", when], id="feed", replace_existing=True,
        )
        log.info("armed feed reminder at %s", run_at.isoformat())

    # Alert titles/messages go to Home Assistant, so they use t() and keep full
    # Unicode + emoji. The OLED banner uses device(): ASCII, max 21 per row.
    def _lang(self) -> str:
        return display.device_lang(self.cfg)

    def _t(self, key: str, **vars) -> str:
        return i18n.t(key, self._lang(), getattr(self.cfg, "data_dir", None), **vars)

    def _d(self, key: str, **vars) -> str:
        return i18n.device(key, self._lang(), getattr(self.cfg, "data_dir", None), **vars)

    async def _fire_pump(self, side: str, pump_time: str) -> None:
        title = "🤱 " + self._t("alert.pumpTitle")
        message = self._t("alert.pumpMsg", side=side, time=pump_time,
                          hours=self._hrs(self.cfg.pump_hours))
        if self.mqtt is not None:
            with contextlib.suppress(Exception):
                await self.mqtt.publish_alert("pump_reminder", title, message, {"side": side})

    async def _fire_feed(self, subtype: str, feed_time: str) -> None:
        title = "🍼 " + self._t("alert.feedTitle")
        what = f" ({subtype})" if subtype else ""
        message = self._t("alert.feedMsg", what=what, time=feed_time,
                          hours=self._hrs(self.cfg.feed_hours))
        # Transient OLED banner on the device (n8n "Notify Device" node).
        # The subtype is a raw DB value ("bottle"/"breast"/"solid"), so it has
        # to be translated too — otherwise a Dutch banner reads half in English.
        if self.mqtt is not None:
            what = self._d("device.sub." + subtype) if subtype else self._d("device.feed")
            await self.mqtt.publish_reminder(
                self._d("device.feedReminder"),
                self._d("device.feedReminderSub", what=what, time=feed_time),
                secs=4)
            with contextlib.suppress(Exception):
                await self.mqtt.publish_alert("feed_reminder", title, message)
