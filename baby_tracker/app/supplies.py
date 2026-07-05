"""Supplies inventory: auto-decrement + refill reminders (SDD-002).

A supply row carries an optional consume rule (`consume_event_type` /
`consume_event_subtype` / `consume_amount`) and two independent reminder
triggers: a low-stock threshold and/or a days cadence.

- `apply_consumption` runs inside the ingest funnel: for each supply whose rule
  matches the just-logged event, subtract `consume_amount` (clamped at 0) and, on
  the first crossing of `low_threshold`, return it so the caller fires a one-shot
  low reminder. The `low_notified` flag debounces until the item is restocked.
- `sweep_reminders` is the daily job: reminds for anything low (once, via the
  flag) or past its refill cadence (daily until refilled).

Pure of MQTT/notify — the caller (main.py / scheduler.py) delivers the message,
so this module stays easy to unit-test.
"""
from __future__ import annotations

import datetime as dt


def _parse(iso: str | None) -> dt.datetime | None:
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def is_low(s: dict) -> bool:
    thr = s.get("low_threshold")
    return thr is not None and (s.get("quantity") or 0) <= thr


def is_due(s: dict, now: dt.datetime | None = None) -> bool:
    days = s.get("refill_days")
    last = _parse(s.get("last_refill_at"))
    if not days or last is None:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    return now >= last + dt.timedelta(days=int(days))


def annotate(s: dict, now: dt.datetime | None = None) -> dict:
    """Return the supply with computed `is_low` / `is_due` flags for the UI."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return {**s, "is_low": is_low(s), "is_due": is_due(s, now)}


def annotate_list(rows: list[dict], now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    return [annotate(s, now) for s in rows]


def _matches(s: dict, event_type: str, event_subtype: str | None) -> bool:
    cet = s.get("consume_event_type")
    if not cet or cet != event_type:
        return False
    cst = s.get("consume_event_subtype")
    return not cst or cst == event_subtype


async def apply_consumption(db, event_type: str,
                            event_subtype: str | None) -> list[dict]:
    """Decrement every supply whose rule matches; return those that just went low.

    Only the FIRST crossing (low_notified 0 -> 1) is returned, so the caller
    fires one reminder per low episode, not one per matching event.
    """
    crossed = []
    for s in await db.list_supplies():
        if not _matches(s, event_type, event_subtype):
            continue
        amount = s.get("consume_amount") or 0
        if amount <= 0:
            continue
        new_qty = max(0.0, (s.get("quantity") or 0) - amount)
        updated = await db.update_supply(s["id"], quantity=new_qty)
        if updated and is_low(updated) and not updated.get("low_notified"):
            await db.update_supply(s["id"], low_notified=1)
            crossed.append(updated)
    return crossed


async def sweep_reminders(db, now: dt.datetime | None = None) -> list[dict]:
    """Daily pass: return supplies needing a reminder, each with a `reasons` list.

    `low` fires once (debounced via low_notified); `due` fires whenever past the
    cadence (a daily nudge until the item is refilled)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for s in await db.list_supplies():
        reasons = []
        if is_low(s) and not s.get("low_notified"):
            await db.update_supply(s["id"], low_notified=1)
            reasons.append("low")
        if is_due(s, now):
            reasons.append("due")
        if reasons:
            out.append({**s, "reasons": reasons})
    return out


async def reconcile_low_flag(db, sid: int) -> dict | None:
    """After a manual/refill quantity change, clear the debounce once restocked
    above the threshold so a future low episode can re-notify."""
    s = await db.get_supply(sid)
    if s and not is_low(s) and s.get("low_notified"):
        return await db.update_supply(sid, low_notified=0)
    return s


def reminder_text(s: dict, reasons: list[str]) -> tuple[str, str]:
    """(title, message) for a supply reminder covering the given reasons."""
    name = s.get("name") or s.get("category") or "Supply"
    qty = s.get("quantity")
    unit = s.get("unit") or ""
    unit_s = f" {unit}" if unit else ""
    if "low" in reasons and "due" in reasons:
        title = f"🧴 {name}: low + refill due"
    elif "low" in reasons:
        title = f"🧴 Low supply: {name}"
    else:
        title = f"🧴 Refill due: {name}"
    bits = []
    if "low" in reasons:
        thr = s.get("low_threshold")
        left = f"{_fmt(qty)}{unit_s} left"
        bits.append(f"{left}" + (f" (refill at {_fmt(thr)}{unit_s})" if thr is not None else ""))
    if "due" in reasons:
        bits.append(f"due for a refill (every {int(s['refill_days'])} days)")
    message = f"{name} is " + "; ".join(bits) + "."
    return title, message


def _fmt(n) -> str:
    if n is None:
        return "0"
    f = float(n)
    return str(int(f)) if f == int(f) else str(f)
