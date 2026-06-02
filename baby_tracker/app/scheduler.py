"""Pump reminders — replaces the n8n "Wait 2h -> notify" flow.

On each pump event we (re)arm a per-side timer; when it fires we send the same
reminder text the n8n flow used.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import notify

log = logging.getLogger("baby.scheduler")


class PumpReminders:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sched = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        if not self.sched.running:
            self.sched.start()

    def shutdown(self) -> None:
        if self.sched.running:
            self.sched.shutdown(wait=False)

    def arm(self, side: str) -> None:
        """Schedule (or reschedule) the reminder for one pump side."""
        side = side or "?"
        now = dt.datetime.now(dt.timezone.utc)
        pump_time = now.astimezone(ZoneInfo(self.cfg.timezone)).strftime("%-I:%M %p")
        run_at = now + dt.timedelta(hours=self.cfg.pump_hours)
        self.sched.add_job(
            self._fire,
            "date",
            run_date=run_at,
            args=[side, pump_time],
            id=f"pump_{side}",
            replace_existing=True,
        )
        log.info("armed pump reminder side=%s at %s", side, run_at.isoformat())

    async def _fire(self, side: str, pump_time: str) -> None:
        title = "🤱 Pump Reminder"
        hrs = int(self.cfg.pump_hours) if self.cfg.pump_hours == int(self.cfg.pump_hours) else self.cfg.pump_hours
        message = (
            f"Time to pump again! Last pump ({side}) was at {pump_time} "
            f"— {hrs} hours ago."
        )
        await notify.notify(self.cfg, title, message)
