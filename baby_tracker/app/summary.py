"""AI daily summaries (SDD-003): build a DE-IDENTIFIED digest of the day, run it
through the configured LLM provider, and store/publish a warm recap.

Privacy is enforced here: `build_digest` emits ONLY aggregate numbers and labels
(counts, sleep, trends, last temp/weight) — never a note, special note, name, or
any free text. `build_prompt` appends that digest to the (editable) instruction,
so the provider never receives identifiable data regardless of the prompt.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from . import llm
from .stats import compute

log = logging.getLogger("baby.summary")


class CapReached(Exception):
    """The local 2/day cap is already used up for today."""


def _parse(iso: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _fmt(n) -> str:
    if n is None:
        return "?"
    return str(int(n)) if float(n) == int(n) else str(round(float(n), 2))


def _day(cfg, now: dt.datetime) -> str:
    return now.astimezone(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")


def _local_time(cfg, now: dt.datetime) -> str:
    t = now.astimezone(ZoneInfo(cfg.timezone))
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'PM' if t.hour >= 12 else 'AM'}"


async def build_digest(db, cfg, now: dt.datetime | None = None) -> dict:
    """De-identified aggregate digest for the LLM. Numbers only, no free text."""
    tz = ZoneInfo(cfg.timezone)
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = await db.recent(500)
    today = compute(rows, cfg.timezone, now)["stats"]

    now_local = now.astimezone(tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    y_start = today_start - dt.timedelta(days=1)

    def local(r):
        return _parse(r["logged_at"]).astimezone(tz)

    def count(etype, start, end, sub=None):
        return sum(1 for r in rows if r["event_type"] == etype
                   and start <= local(r) < end
                   and (sub is None or r.get("event_subtype") == sub))

    # feed subtype breakdown + yesterday comparison
    feed_breakdown = {s: count("feed", today_start, now_local + dt.timedelta(minutes=1), s)
                      for s in ("breast", "bottle", "solid")}
    feeds_yesterday = count("feed", y_start, today_start)
    diapers_yesterday = count("diaper", y_start, today_start)

    # average feed gap today (minutes)
    feed_ts = sorted(local(r).timestamp() for r in rows
                     if r["event_type"] == "feed" and today_start <= local(r))
    avg_feed_gap = None
    if len(feed_ts) >= 2:
        gaps = [(feed_ts[i + 1] - feed_ts[i]) / 60.0 for i in range(len(feed_ts) - 1)]
        avg_feed_gap = round(sum(gaps) / len(gaps), 1)

    # last temperature (+ fever)
    last_temp = None
    for r in rows:
        if r.get("event_type") == "temperature" and r.get("value") is not None:
            v, u = r["value"], (r.get("value_unit") or "")
            c = (v - 32) * 5 / 9 if "F" in u else v
            last_temp = {"value": v, "unit": u, "fever": c >= cfg.fever_threshold_c}
            break

    # latest growth metric + delta
    growth = {}
    for m in ("weight", "length", "head_circumference"):
        series = await db.metric_series(m, 5)
        if series:
            last = series[-1]
            delta = round(last["value"] - series[-2]["value"], 2) if len(series) >= 2 else None
            growth[m] = {"value": last["value"], "unit": last.get("value_unit"), "delta": delta}

    return {
        "feeds_today": today["feeds_today"],
        "feeds_by": feed_breakdown,
        "feeds_yesterday": feeds_yesterday,
        "avg_feed_gap_min": avg_feed_gap,
        "diapers_today": today["diapers_today"],
        "diapers_yesterday": diapers_yesterday,
        "sleep_today": today["sleep_total_today"],
        "is_sleeping": today["is_sleeping"],
        "pumps_today": today["pumps_today"],
        "baths_today": today["baths_today"],
        "tummy_today": today["tummy_times_today"],
        "medicines_today": today["medicines_today"],
        "contractions_today": today.get("contractions_today", 0),
        "last_temp": last_temp,
        "growth": growth,
    }


def render_digest(d: dict) -> str:
    lines = []
    fb = d["feeds_by"]
    parts = [f"{k} {v}" for k, v in fb.items() if v]
    lines.append(f"Feeds today: {d['feeds_today']}"
                 + (f" ({', '.join(parts)})" if parts else "")
                 + f" [yesterday {d['feeds_yesterday']}]")
    if d["avg_feed_gap_min"] is not None:
        lines.append(f"Average gap between feeds today: {d['avg_feed_gap_min']} min")
    lines.append(f"Diapers today: {d['diapers_today']} [yesterday {d['diapers_yesterday']}]")
    lines.append(f"Sleep today: {d['sleep_today']}"
                 + (" (currently sleeping)" if d["is_sleeping"] else ""))
    lines.append(f"Pumps {d['pumps_today']}, baths {d['baths_today']}, "
                 f"tummy time {d['tummy_today']}, medicines {d['medicines_today']}")
    if d["contractions_today"]:
        lines.append(f"Contractions today: {d['contractions_today']}")
    if d["last_temp"]:
        t = d["last_temp"]
        lines.append(f"Last temperature: {_fmt(t['value'])} {t['unit']}"
                     + (" (FEVER)" if t["fever"] else ""))
    labels = {"weight": "Weight", "length": "Length", "head_circumference": "Head"}
    for k, g in d["growth"].items():
        delta = f" (change {'+' if (g['delta'] or 0) >= 0 else ''}{_fmt(g['delta'])} {g['unit']})" \
            if g["delta"] is not None else ""
        lines.append(f"{labels[k]}: {_fmt(g['value'])} {g['unit']}{delta}")
    return "\n".join(lines)


def build_prompt(cfg, digest: dict) -> str:
    return f"{cfg.summary_prompt}\n\nToday's data:\n{render_digest(digest)}"


async def generate(db, cfg, mqtt=None, install_token: str | None = None,
                   source: str = "manual", now: dt.datetime | None = None) -> dict | None:
    """Run one summary. Returns the stored row, or None when disabled.

    Raises `CapReached` when the local daily cap is used up, or `llm.CapError` /
    `llm.ProviderError` on provider issues (the caller maps these to responses).
    """
    if not cfg.summary_enabled:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    day = _day(cfg, now)
    if await db.count_summaries_today(day) >= cfg.summary_daily_cap:
        raise CapReached(day)
    digest = await build_digest(db, cfg, now)
    text = (await llm.generate(cfg, build_prompt(cfg, digest), install_token)) or ""
    text = text.strip() or "No summary available."
    row = await db.insert_summary(text, cfg.summary_provider, source, day)
    if mqtt is not None:
        await mqtt.publish_summary(text, _local_time(cfg, now), source)
    log.info("summary[%s] via %s: %s", source, cfg.summary_provider, text[:80])
    return row
