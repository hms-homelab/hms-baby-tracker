"""Contraction AI assessment — a faithful port of the n8n "Contraction AI
Assessment" workflow (id 46kd4mI38nbHVM6t) into the add-on.

The n8n flow was webhook-triggered: on each fire it queried the last 2 hours of
contractions, computed gap/intensity stats, asked Ollama for a 2-sentence labor
assessment, and pushed the result into two HA `input_text` entities
(`input_text.ai_assessment` + `input_text.ai_assessment_time`).

Here it is event-driven the same way: `maybe_assess` is called from the ingest
funnel whenever a `contraction` event lands (the moral equivalent of the n8n
webhook), gated entirely behind `ollama_enabled` so it never runs for parents
without an LLM. Output is published two ways (both best-effort):

  * an HA MQTT-discovery text/sensor (`sensor.baby_contraction_assessment`
    + `sensor.baby_contraction_assessment_time`) — the add-on's native path;
  * the original `input_text.ai_assessment` / `input_text.ai_assessment_time`
    via the Supervisor core proxy, so an existing HA dashboard bound to those
    entities keeps working unchanged.

Stat math (gaps, intensity map/labels/breakdown, the <2-contractions skip
message, the exact prompt wording) is copied verbatim from the n8n "Build
Prompt" Code node. Intensity is read from `event_subtype` (or `note`) when it is
one of mild/moderate/strong/intense; the real `baby_events` archive only stores
the literal subtype "contraction", so intensity resolves to "not rated" there —
matching what the workflow produced for the same data.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("baby.assessment")

SUPERVISOR_CORE = "http://supervisor/core/api"

# The two HA input_text entities the n8n workflow wrote to. Preserved so an
# existing dashboard binding keeps working after n8n is retired.
HA_TEXT_ENTITY = "input_text.ai_assessment"
HA_TIME_ENTITY = "input_text.ai_assessment_time"

_INTENSITY_MAP = {"mild": 1, "moderate": 2, "strong": 3, "intense": 4}
_INTENSITY_LABELS = ["mild", "moderate", "strong", "intense"]

NO_DATA_MSG = "Need 2+ contractions in 2h"


def _round1(x: float) -> float:
    """JS `Math.round(x*10)/10` — one-decimal rounding, half toward +inf."""
    import math
    return math.floor(x * 10 + 0.5) / 10


def _parse(iso: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _local_time(tz: ZoneInfo, now: dt.datetime | None = None) -> str:
    """Mirror the n8n localTime: en-US 12h '02:05 PM' in the configured tz."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(tz).strftime("%I:%M %p")


def _intensity_of(row: dict) -> str | None:
    """Resolve a contraction's intensity from subtype/note if it names one."""
    for key in (row.get("event_subtype"), row.get("note")):
        if key and str(key).lower() in _INTENSITY_MAP:
            return str(key).lower()
    return None


def build_prompt(recent: list[dict], tz: ZoneInfo,
                 now: dt.datetime | None = None,
                 prompt_override: str | None = None) -> dict:
    """Port of the n8n "Build Prompt" node.

    `recent` is most-recent-first contraction rows (each with `logged_at` and
    optionally `event_subtype`/`note` carrying an intensity). Returns
    {skip, prompt|assessment, localTime}.
    """
    local_time = _local_time(tz, now)
    if len(recent) < 2:
        return {"skip": True, "assessment": NO_DATA_MSG, "localTime": local_time}

    times = sorted((_parse(e["logged_at"]).timestamp() for e in recent), reverse=True)
    gaps = [(times[i] - times[i + 1]) / 60.0 for i in range(len(times) - 1)]
    avg_gap = _round1(sum(gaps) / len(gaps))

    with_i = [i for e in recent if (i := _intensity_of(e))]
    avg_i = None
    i_label = None
    if with_i:
        avg_i = _round1(sum(_INTENSITY_MAP[i] for i in with_i) / len(with_i))
        i_label = _INTENSITY_LABELS[round(avg_i) - 1]

    breakdown: dict[str, int] = {}
    for e in recent:
        k = _intensity_of(e) or "unrated"
        breakdown[k] = breakdown.get(k, 0) + 1

    if prompt_override:
        prompt = prompt_override.format(
            count=len(recent), avg_gap=avg_gap,
            avg_intensity=(avg_i if avg_i is not None else "not rated"),
            intensity_label=(i_label or "n/a"),
            breakdown=json.dumps(breakdown),
            shortest=_round1(min(gaps)), longest=_round1(max(gaps)),
        )
    else:
        prompt = (
            "You are a labor assessment assistant. Based on contraction data from "
            "the last 2 hours, give a brief assessment (2 sentences max). Include "
            "the likely labor stage and one practical suggestion.\n\n"
            "Data:\n"
            f"- {len(recent)} contractions in last 2 hours\n"
            f"- Average gap: {avg_gap} minutes\n"
            f"- Average intensity: {avg_i if avg_i is not None else 'not rated'}/4 "
            f"({i_label or 'n/a'})\n"
            f"- Intensity breakdown: {json.dumps(breakdown)}\n"
            f"- Shortest gap: {_round1(min(gaps))} min, longest: {_round1(max(gaps))} min\n\n"
            "Respond with ONLY the assessment, no disclaimers. Max 2 sentences."
        )
    return {"skip": False, "prompt": prompt, "localTime": local_time}


async def _recent_contractions(db, tz: ZoneInfo, window_hours: float = 2.0,
                               now: dt.datetime | None = None) -> list[dict]:
    """Last `window_hours` of contraction rows, most-recent-first.

    Reuses the backend-agnostic `db.recent()` (no extra DB methods needed) and
    filters in Python, matching the n8n query semantics
    (event_type='contraction', logged_at > now - 2h).
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=window_hours)
    rows = await db.recent(500)
    return [
        r for r in rows
        if r.get("event_type") == "contraction" and _parse(r["logged_at"]) > cutoff
    ]


async def call_ollama(cfg, prompt: str) -> str:
    """POST /api/generate (stream=false) and return the response text."""
    url = cfg.ollama_url.rstrip("/") + "/api/generate"
    body = {"model": cfg.ollama_model, "prompt": prompt, "stream": False}
    async with httpx.AsyncClient(timeout=cfg.ollama_timeout) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        return (r.json().get("response") or "No response").strip()


async def _set_ha_input_text(cfg, entity_id: str, value: str) -> None:
    """Best-effort write to an HA input_text via the Supervisor core proxy."""
    if not cfg.supervisor_token:
        return
    headers = {"Authorization": f"Bearer {cfg.supervisor_token}"}
    body = {"entity_id": entity_id, "value": value[:255]}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{SUPERVISOR_CORE}/services/input_text/set_value",
                json=body, headers=headers)
            if r.status_code >= 400:
                log.debug("input_text %s -> %s %s", entity_id, r.status_code, r.text[:160])
    except httpx.HTTPError as e:
        log.debug("input_text %s failed: %s", entity_id, e)


async def maybe_assess(cfg, db, mqtt=None, now: dt.datetime | None = None) -> dict | None:
    """Run one assessment cycle (the n8n webhook body). No-op unless enabled.

    Returns the published {assessment, time, skipped} dict, or None when the
    feature is disabled. Never raises into the caller's event path.
    """
    if not getattr(cfg, "ollama_enabled", False):
        return None
    tz = ZoneInfo(cfg.timezone)
    try:
        recent = await _recent_contractions(db, tz, now=now)
        built = build_prompt(recent, tz, now=now,
                             prompt_override=getattr(cfg, "ollama_prompt", None) or None)
        if built["skip"]:
            assessment = built["assessment"]
        else:
            assessment = await call_ollama(cfg, built["prompt"])
        local_time = built["localTime"]
    except Exception as e:  # don't let an LLM/DB hiccup kill the event path
        log.warning("contraction assessment failed: %s", e)
        return None

    # Output 1: native MQTT-discovery sensors (published by mqtt bridge).
    if mqtt is not None:
        await mqtt.publish_assessment(assessment, local_time)
    # Output 2: the original HA input_text entities (preserve dashboards).
    await _set_ha_input_text(cfg, HA_TEXT_ENTITY, assessment)
    await _set_ha_input_text(cfg, HA_TIME_ENTITY, local_time)

    log.info("contraction assessment (%d in 2h): %s", len(recent), assessment[:80])
    return {"assessment": assessment, "time": local_time, "skipped": built["skip"]}
