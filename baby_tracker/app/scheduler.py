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

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import display, notify

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

    async def refresh_display(self) -> None:
        """Recompute + publish the device OLED rows and pump-due alert flag."""
        if self.mqtt is None or self.db is None:
            return
        try:
            payloads = await display.compute_payloads(self.db, self.cfg)
            await self.mqtt.publish_display(payloads)
        except Exception as e:  # never let a poll error kill the scheduler
            log.warning("display refresh failed: %s", e)

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

    async def _fire_pump(self, side: str, pump_time: str) -> None:
        title = "🤱 Pump Reminder"
        message = (
            f"Time to pump again! Last pump ({side}) was at {pump_time} "
            f"— {self._hrs(self.cfg.pump_hours)} hours ago."
        )
        await notify.notify(self.cfg, title, message)

    async def _fire_feed(self, subtype: str, feed_time: str) -> None:
        title = "🍼 Feed Reminder"
        what = f" ({subtype})" if subtype else ""
        message = (
            f"Time to feed again! Last feed{what} was at {feed_time} "
            f"— {self._hrs(self.cfg.feed_hours)} hours ago."
        )
        await notify.notify(self.cfg, title, message)
        # Transient OLED banner on the device (n8n "Notify Device" node).
        if self.mqtt is not None:
            await self.mqtt.publish_reminder(
                "Feed reminder", f"last {subtype or 'feed'} {feed_time}", secs=4)
