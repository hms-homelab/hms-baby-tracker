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
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import display, i18n, supplies, timefmt
from .timefmt import clock

log = logging.getLogger("baby.scheduler")

# A reminder series that came due while the add-on was restarting still fires,
# as long as it is less than an hour late (coalesced into a single run). The
# APScheduler default would silently drop it.
SERIES_GRACE = 3600


def parse_iso(value: str | None) -> dt.datetime | None:
    """Parse an ISO8601 string to an aware UTC-anchored datetime (None-safe)."""
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d


def at_time_text(at_time: str | None, fmt: str = timefmt.TWELVE) -> str:
    """Render a stored `HH:MM` through the app's one clock (SDD-006), so a daily
    series reads "8:00 AM" or "08:00" like every other time the app prints."""
    try:
        hour, minute = (int(x) for x in str(at_time or "").split(":")[:2])
        return timefmt.clock(dt.time(hour=hour, minute=minute), fmt)
    except (ValueError, TypeError):
        return str(at_time or "")


def every_text(minutes: int) -> str:
    """Humanize an interval: 90 -> '1h 30m', 360 -> '6h', 45 -> '45m'."""
    minutes = max(1, int(minutes))
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


class Reminders:
    def __init__(self, cfg, mqtt=None, db=None):
        self.cfg = cfg
        self.mqtt = mqtt  # MqttBridge, for device display/reminder/alert
        self.db = db      # Database, for the periodic display poll
        self.sched = AsyncIOScheduler(timezone="UTC")
        # Optional `async (rid) -> str | None` returning a tappable link to the
        # series in the web UI. Set by main.py once the add-on knows its own
        # Supervisor slug; stays None standalone, where there is no HA to open.
        self.deep_link = None

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

    # --- custom reminder series (SDD-007) ----------------------------------
    # A series is a user-created repeating alert ("Tylenol every 6h"), armed
    # from a journal row or the Reminders card. The DB row is the source of
    # truth; the APScheduler job is a rebuildable mirror of it, re-armed on
    # every start by load_series().
    @staticmethod
    def series_job_id(rid) -> str:
        return f"series_{int(rid)}"

    def _series_trigger(self, row: dict):
        """Build the trigger for one series, or None if it can't/shouldn't run.

        Interval series start one full interval from now (or from the row's
        `start_at` when it is still in the future), so arming "every 6h" at 2pm
        first fires at 8pm — never instantly.
        """
        stop = parse_iso(row.get("stop_at"))
        now = dt.datetime.now(dt.timezone.utc)
        if stop and stop <= now:
            return None
        if (row.get("mode") or "interval") == "daily":
            at = str(row.get("at_time") or "")
            try:
                hour, minute = (int(x) for x in at.split(":")[:2])
            except (ValueError, TypeError):
                return None
            return CronTrigger(hour=hour, minute=minute, timezone=self.cfg.timezone,
                               end_date=stop)
        minutes = int(row.get("interval_min") or 0)
        if minutes < 1:
            return None
        start = parse_iso(row.get("start_at")) or now
        first = start + dt.timedelta(minutes=minutes)
        # Catch up a series armed before a long outage: roll forward to the next
        # slot in the future instead of firing a burst of missed ones.
        if first <= now:
            missed = int((now - first).total_seconds() // (minutes * 60)) + 1
            first += dt.timedelta(minutes=minutes * missed)
        if stop and first >= stop:
            return None
        return IntervalTrigger(minutes=minutes, start_date=first, end_date=stop)

    def schedule_series(self, row: dict) -> dt.datetime | None:
        """(Re)arm one series. Returns its next run, or None when not armed."""
        rid = row.get("id")
        if rid is None:
            return None
        self.unschedule_series(rid)
        if not row.get("enabled"):
            return None
        trigger = self._series_trigger(row)
        if trigger is None:
            return None
        job = self.sched.add_job(
            self._fire_series, trigger, args=[int(rid)],
            id=self.series_job_id(rid), replace_existing=True,
            coalesce=True, misfire_grace_time=SERIES_GRACE,
        )
        log.info("armed reminder series #%s (%s) next=%s", rid, row.get("title"),
                 getattr(job, "next_run_time", None))
        return getattr(job, "next_run_time", None)

    def unschedule_series(self, rid) -> None:
        with contextlib.suppress(Exception):
            self.sched.remove_job(self.series_job_id(rid))

    def clear_series(self) -> None:
        """Drop every armed series job (used before a restore rebuilds them)."""
        for job in list(self.sched.get_jobs()):
            if str(job.id).startswith("series_"):
                with contextlib.suppress(Exception):
                    job.remove()

    def series_next_run(self, rid) -> str | None:
        job = self.sched.get_job(self.series_job_id(rid))
        run_at = getattr(job, "next_run_time", None) if job else None
        return run_at.isoformat() if run_at else None

    async def reanchor_series(self, rid) -> str | None:
        """Restart a series' countdown from now, because the dose it was asking
        for was just logged (SDD-008).

        Without this, "every 6h" runs on the grid it was armed on: give the dose
        40 minutes late and the next alert is still only 5h20m away, and the gap
        keeps shrinking every time. Re-anchoring makes the interval mean what a
        parent reads it to mean — six hours from the dose actually given.

        Daily series are a fixed wall-clock time, so there is nothing to move:
        they are left alone. Returns the new next-run ISO string, or None.
        """
        if self.db is None:
            return None
        try:
            row = await self.db.get_reminder(rid)
        except Exception as e:
            log.warning("reminder #%s lookup failed: %s", rid, e)
            return None
        if not row or not row.get("enabled"):
            return None
        if (row.get("mode") or "interval") == "daily":
            return self.series_next_run(rid)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with contextlib.suppress(Exception):
            await self.db.update_reminder(rid, start_at=now)
        self.schedule_series({**row, "start_at": now})
        nxt = self.series_next_run(rid)
        log.info("re-anchored reminder series #%s (%s) next=%s", rid,
                 row.get("title"), nxt)
        return nxt

    def snooze_series(self, rid, minutes: int = 15) -> str | None:
        """Push a series' next alert out by `minutes` without logging a dose.

        Only the pending fire moves; the row is untouched, so a series that is
        snoozed and then never logged still carries on from the snoozed alert.
        """
        minutes = max(1, int(minutes))
        job = self.sched.get_job(self.series_job_id(rid))
        if job is None:
            return None
        when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)
        with contextlib.suppress(Exception):
            job.modify(next_run_time=when)
        log.info("snoozed reminder series #%s for %sm -> %s", rid, minutes, when)
        return self.series_next_run(rid)

    async def load_series(self) -> None:
        """Re-arm every stored series on startup (jobs live in memory only)."""
        if self.db is None:
            return
        try:
            rows = await self.db.list_reminders()
        except Exception as e:
            log.warning("could not load reminder series: %s", e)
            return
        for row in rows:
            with contextlib.suppress(Exception):
                self.schedule_series(row)

    async def _fire_series(self, rid: int) -> None:
        """Deliver one series alert and record the run.

        The row is re-read at fire time so an edited title or a series stopped
        from another device takes effect without a restart.
        """
        if self.db is None:
            return
        try:
            row = await self.db.get_reminder(rid)
        except Exception as e:
            log.warning("reminder #%s lookup failed: %s", rid, e)
            return
        if not row or not row.get("enabled"):
            self.unschedule_series(rid)
            return
        stop = parse_iso(row.get("stop_at"))
        now = dt.datetime.now(dt.timezone.utc)
        if stop and stop <= now:
            self.unschedule_series(rid)
            with contextlib.suppress(Exception):
                await self.db.update_reminder(rid, enabled=0)
            return

        name = row.get("title") or self._t("alert.reminderTitle")
        title = "⏰ " + name
        if (row.get("mode") or "interval") == "daily":
            message = self._t("alert.reminderDaily", title=name,
                              time=at_time_text(row.get("at_time"),
                                                getattr(self.cfg, "time_format", "12h")))
        else:
            message = self._t("alert.reminderMsg", title=name,
                              every=every_text(row.get("interval_min") or 0))
        extra = {"reminder_id": int(rid), "reminder_title": name,
                 "event_type": row.get("event_type"),
                 "event_subtype": row.get("event_subtype")}
        # A tappable link straight to this series in the web UI, so the alert
        # lands on the record instead of the app's front door (SDD-008).
        if self.deep_link is not None:
            with contextlib.suppress(Exception):
                url = await self.deep_link(int(rid))
                if url:
                    extra["url"] = url
        if self.mqtt is not None:
            with contextlib.suppress(Exception):
                await self.mqtt.publish_alert("reminder", title, message, extra)
        with contextlib.suppress(Exception):
            await self.db.update_reminder(
                rid, last_fired_at=now.isoformat(),
                fire_count=int(row.get("fire_count") or 0) + 1,
            )
        # The last run of a series with a stop date: retire the row so the card
        # shows it as finished instead of pretending it is still armed.
        if stop and self.series_next_run(rid) is None:
            with contextlib.suppress(Exception):
                await self.db.update_reminder(rid, enabled=0)

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
        local = clock(now.astimezone(ZoneInfo(self.cfg.timezone)),
                      getattr(self.cfg, "time_format", "12h"))
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
