"""Reminders — replaces the n8n "Wait Nh -> notify" flows.

On each pump event we (re)arm a per-side pump timer; on each feed event we
(re)arm a single feed timer. When a timer fires we send the same reminder text
the n8n flows used. A newer event of the same kind reschedules (replaces) the
job, so only the latest pump/feed fires — any feed resets the feed clock.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import notify

log = logging.getLogger("baby.scheduler")


class Reminders:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sched = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        if not self.sched.running:
            self.sched.start()

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
