# SDD-006 — 24-hour time format

Status: **IMPLEMENTED**. All five §7 decisions resolved 2026-08-28. Built and
verified the same day; 137 tests green (98 existing + 39 new).
Date: 2026-08-28
Component: `baby_tracker/` (`app/db.py`, `app/ingest.py`, `app/scheduler.py`,
`app/summary.py`, `app/assessment.py`, `app/config.py`, `app/main.py`, `web/app.js`)
Origin: GitHub issue [#10](https://github.com/hms-homelab/hms-baby-tracker/issues/10)
(mkampstra / Merte): "The app now is in AM/PM (12h mode). It would be great to
have a 24h mode. So like half past 8 in the evening right now shows as 8:30 PM.
In europe 20:30u is more common and would be a great extra setting."
Ships as: **2026.4.15** (patch, staying on the 4.x line). Drafted against
2026.4.12 as SDD-005 and renumbered on rebase: another session shipped 2026.4.13
and 2026.4.14 and took the SDD-005 slot while this was in flight.

## 1. Goal

One add-on option, `time_format`, that switches every clock the app prints from
`8:30 PM` to `20:30`. Every surface follows it together: the journal, Home
Assistant notifications, pump and feed reminders, the MQTT sensor attributes and
the AI summary card.

The default stays `12h`, so an existing install that never opens the option page
sees byte-identical output to what shipped before this change.

## 2. Background: where the 12h clock actually lives

The reporter says "the app is in AM/PM", which reads like a UI-side format
choice. It is not. **Most of the clock strings are rendered on the server** and
arrive at the browser already formatted, so a browser-only flag would leave the
journal untouched. This is the fact that shapes the whole design.

Full inventory, every site verified:

| # | Site | Produces | Reaches |
|---|---|---|---|
| 1 | `app/db.py:88` `_fmt_time` | `"7:42 PM, Aug 28"` | the `time` field on every event row |
| 2 | `app/ingest.py:55` in `format_event` | `"🍼 Feed at 8:30 PM"` | HA persistent notification title/message, MQTT |
| 3 | `app/scheduler.py:109` `_now` | `"8:30 PM"` | pump/feed reminder text and OLED banner |
| 4 | `app/summary.py:73` `_local_time` | `"6:05 AM"` | `baby/summary` retained payload, `time` field |
| 5 | `app/assessment.py:69` `_local_time` | `"02:05 PM"` | contraction AI prompt + `sensor.baby_contraction_assessment_time` |
| 6 | `web/app.js:521` `fmtClock` | `"6:05 AM"` | the AI summary card's "generated 6:05 AM" |

Site 1 is the one the reporter is looking at. `_fmt_time` is called from four
places, two per backend:

- `SqliteDatabase.recent` (`db.py:245`) and `PostgresDatabase.recent` (`db.py:740`)
- `SqliteDatabase.metric_series` (`db.py:277`) and `PostgresDatabase.metric_series` (`db.py:767`)

`recent` feeds `stats.compute` (`stats.py:111` copies `time` straight through
into `entries`), which is what the journal list renders at `app.js:415`
(`time.textContent = e.time`). `metric_series` feeds the Health and Growth tabs
(`app.js:877`, `app.js:888`).

### 2.1 What is already 24h and needs nothing

- **The `datetime-local` pickers.** `tzInput` (`app.js:135`) and `tzOffsetMin`
  (`app.js:150`) both pass `hour12: false` deliberately, because they build a
  `YYYY-MM-DDTHH:MM` wire value, not display text. The widget's own rendering is
  the browser's business and already follows the OS locale.
- **`app/display.py`.** The OLED prints elapsed time (`_ago_str`), never a
  clock. No `device.*` key carries a wall-clock time, so SDD-004's ASCII fold
  and 21-character budget are not in play here.
- **`fmtAgo` and the "x min ago" strings.** Durations, not clocks.

### 2.2 Two pre-existing inconsistencies this touches

Worth naming, because a reviewer will hit them:

- **Leading zero.** `db._fmt_time`, `ingest.format_event`, `scheduler._now` and
  `summary._local_time` all strip the leading zero (`7:42 PM`, per issue #2).
  `assessment._local_time` uses `strftime("%I:%M %p")` and does not (`02:05 PM`).
  Routing all five through one helper fixes that as a side effect.
- **Dead constant.** `db.py:22` `_TIME_FMT = "%I:%M %p, %b %d"` is defined and
  referenced nowhere. It is a leftover from the n8n port. Delete it rather than
  leave a second, stale definition of the format next to the live one.

## 3. Design

### 3.1 The option

```yaml
# config.yaml options
time_format: 12h

# config.yaml schema
# Clock style for logged times, reminders and notifications.
# 12h = "8:30 PM", 24h = "20:30".
time_format: list(12h|24h)
```

`app/config.py` gains one field and one `load()` line, matching how
`measurement_system` and `language` are already wired:

```python
# Clock style for every time the app prints: "12h" (8:30 PM) or "24h" (20:30).
# Server-rendered, because the journal's `time` string is built in db.py, so
# this is one setting for the whole install rather than a per-browser pick.
time_format: str = "12h"
...
time_format=(opts.get("time_format") or env.get("TIME_FORMAT") or "12h"),
```

### 3.2 One helper, five call sites

New `app/timefmt.py`, so there is exactly one definition of the clock:

```python
def clock(d: dt.datetime, fmt: str = "12h") -> str:
    """Wall clock of an already-localized datetime.

    12h -> "8:30 PM" (no leading zero, issue #2)
    24h -> "20:30"   (zero-padded, which is the convention the format implies)
    """
    if fmt == "24h":
        return f"{d.hour:02d}:{d.minute:02d}"
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'PM' if d.hour >= 12 else 'AM'}"
```

Pure, no config import, takes an aware datetime already in the target zone. That
keeps it trivially testable and keeps `db.py` free of a config dependency.

Then:

| Site | Change |
|---|---|
| `db.py` | `_fmt_time(iso, tz, fmt)` calls `clock()`, returns `f"{clock(d, fmt)}, {d.strftime('%b %d')}"`. `Database(path, timezone, database_url, time_format="12h")` stores `self.time_fmt`; both backends pass it at the four call sites. |
| `ingest.py` | `format_event(..., timezone, value, value_unit, time_format="12h")` calls `clock()`. `create_event` passes `cfg.time_format`. |
| `scheduler.py` | `_now()` calls `clock(now.astimezone(tz), self.cfg.time_format)`. |
| `summary.py` | `_local_time(cfg, now)` calls `clock()` with `cfg.time_format`. |
| `assessment.py` | `_local_time(tz, now, fmt)` calls `clock()`; `build_prompt` takes the format through to `localTime`. |

Every signature gains a **keyword argument with a `"12h"` default**, so existing
call sites and the 98 current tests keep working unchanged, and only the ones
asserting on a 24h path need touching.

### 3.3 The client

`GET /api/config` (`main.py:366`) gains `"time_format": fmt`, validated the same
way `default_tab` and `measurement_system` already are:

```python
fmt = cfg.time_format if cfg.time_format in ("12h", "24h") else "12h"
```

`app.js` stores it next to `appTz` and `fmtClock` branches on it. That is the
only client change: six of the six display sites, one is client-side.

### 3.4 What is deliberately not per-browser

SDD-004 gave the **language** a per-browser override in `localStorage`, and the
obvious question is why the clock does not get the same treatment.

Because the two are not the same shape. The language picker works per browser
because `applyDom()` re-renders text the client already holds. The journal's
time is a **string the server built**; making it per-browser means the SPA stops
rendering `e.time` and reformats from `logged_at` + `appTz` itself, and the
Health and Growth tabs follow. That is a real change to the render path for a
setting that, unlike language, two parents sharing an install are unlikely to
disagree on.

Meanwhile the notification text, the reminder text and the MQTT sensor
attributes are produced server-side and no browser can move them. A per-browser
clock would therefore show `20:30` in the journal and `8:30 PM` in the Home
Assistant notification for the same event, which reads as a bug.

So: one install-wide setting, every surface consistent. Recorded here because
"why isn't this in the language menu like the flag is" is the first question a
reader will have. If a per-browser override is wanted later, §6 notes what it
would cost.

## 4. i18n interaction

**No catalog changes.** No new key, no changed key, no `en.json` edit.

- `AM`/`PM` are not catalog entries today; they are literals inside the Python
  and JS formatters, and in 24h mode they disappear rather than translate.
- `"ai.generated": "generated {time}"` interpolates whatever `fmtClock` returns,
  so it is already format-agnostic in all four languages.
- No `device.*` key carries a clock, so the ASCII and 21-character tests are
  unaffected.

This matters operationally: SDD-004's completeness test (`test_i18n.py`, 29
tests) asserts every locale's key set equals `en.json`'s. Adding a key here
would mean touching `nl`, `es` and `fr` too. Adding none means CI stays green
without a translation round trip.

## 5. Tests

New `tests/test_timefmt.py`:

1. **`clock()` unit cases.** `12h`: `00:00 -> "12:00 AM"`, `00:30 -> "12:30 AM"`,
   `12:00 -> "12:00 PM"`, `13:05 -> "1:05 PM"`, `20:30 -> "8:30 PM"`,
   `07:42 -> "7:42 AM"` (no leading zero). `24h`: the same instants as
   `"00:00"`, `"00:30"`, `"12:00"`, `"13:05"`, `"20:30"`, `"07:42"`
   (zero-padded). Midnight and noon are the two that formatters get wrong.
2. **Unknown value falls back to 12h.** `clock(d, "banana")` and
   `clock(d, "")` return the 12h string. An option file can hold anything.
3. **`db._fmt_time` both ways.** `"20:30, Aug 28"` under `24h`,
   `"8:30 PM, Aug 28"` under `12h`. The date half is untouched by the setting.
4. **`Database(..., time_format="24h").recent()`** returns 24h `time` fields, and
   **`metric_series`** does too, so the Health and Growth tabs follow. SQLite
   path in CI; the Postgres path is the same two lines and is asserted by reading
   `self.time_fmt`, not by standing up Postgres.
5. **`format_event` both ways.** `"🍼 Feed at 20:30"` vs `"🍼 Feed at 8:30 PM"`,
   with the value suffix (`(4.2 kg)`) and the note line intact in both.
6. **`scheduler._now`** returns 24h when `cfg.time_format = "24h"`.
7. **`summary._local_time`** and **`assessment._local_time`** both ways, and
   `build_prompt`'s `localTime` carries through.
8. **Default is 12h everywhere.** Construct each of the five with no
   `time_format` argument and assert the pre-change string. This is the
   regression guard for the "existing installs see no change" promise.
9. **`/api/config` returns `time_format`**, and returns `12h` for a garbage
   option value.
10. **`assessment` leading zero is gone.** `13:05` renders `"1:05 PM"`, not
    `"01:05 PM"`, matching the other four (§2.2).

Existing **98 tests must stay green**. The ones most likely to need a look are
`test_phase2.py:55` (`test_format_event_includes_value`) and the `test_summary`
/ `test_assessment` sets, all of which assert on 12h strings and should keep
doing so, since the default does not move.

## 6. Out of scope, and why

- **A per-browser override**, as argued in §3.4. It would mean the SPA
  reformatting the journal, Health and Growth times from `logged_at` instead of
  the server's `time` string, and it still could not move the notifications.
  Cheap to add later on top of this option; not free, and not obviously wanted.
- **Date format.** The reporter asked about the clock. `%b %d` ("Aug 28") stays
  as it is. `28-08` vs `08-28` is a separate, larger question that touches the
  same four `_fmt_time` call sites and can reuse this plumbing if it comes up.
- **Seconds.** Nothing in the app displays them.
- **The HA Configuration panel's option label.** Same call as SDD-004 §6: HA's
  native `translations/<lang>.yaml` mechanism exists, but the panel stays English
  for now.
- **`sensor.baby_contraction_assessment_time`'s historical values.** The setting
  changes what is published from the flip onward. Values already retained in HA
  keep their old format until the next event.

## 7. Decisions (resolved 2026-08-28)

All five confirmed as recommended.

1. **Option values: `12h` / `24h`, no `auto`.** An `auto` would have
   to derive the clock from `cfg.language`, mapping `nl`/`fr`/`es` to 24h and
   `en` to 12h, and that mapping is wrong for at least two real cases: `en-GB`
   uses 24h and `es-US` uses 12h. The heuristic would be silently wrong for the
   users it guessed at, and the server has no browser locale to consult for the
   journal string anyway. Two explicit values, defaulting to today's behaviour.
2. **Default `12h`.** On no-surprise grounds: the option is new, and every
   existing install would otherwise have its journal change format on an update
   it did not ask for.
3. **Zero-pad the 24h hour (`08:30`, not `8:30`).** It is the convention the
   format implies, and issue #2's no-leading-zero fix was specifically about the
   12h form.
4. **Version 2026.4.15.** Patch, 4.x line, consistent with SDD-003 and SDD-004.
   Approved as 2026.4.13; renumbered on rebase because another session had
   already shipped 2026.4.13 and 2026.4.14. `VERSION` and `config.yaml` are
   bumped together at Albin's instruction, ahead of the ghcr images rather than
   after them, which the two-phase note in `config.yaml` normally advises
   against.
5. **Deleted the dead `_TIME_FMT` at `db.py:22`** (§2.2).

## 7.1 What shipped

- New `app/timefmt.py`: `clock(d, fmt)` and `normalize(fmt)`. Pure, no config
  import, takes an already-localized aware datetime.
- `app/config.py`: `time_format: str = "12h"` plus a `TIME_FORMAT` env fallback.
- `config.yaml`: option and `list(12h|24h)` schema entry.
- `app/db.py`: `_TIME_FMT` deleted; `_fmt_time` and both backend constructors
  take the format; all four call sites pass `self.time_fmt`.
- `app/ingest.py`, `app/scheduler.py`, `app/summary.py`, `app/assessment.py`:
  each stripped of its own AM/PM arithmetic and routed through `clock()`.
  `build_prompt` carries the format through to `localTime`.
- `app/main.py`: `Database(...)` gets `cfg.time_format`; `/api/config` returns a
  normalized `time_format`.
- `web/app.js`: `timeFmt` from `/api/config`, `fmtClock` branches on it.
- `tests/test_timefmt.py`: 39 tests.

Verified against a running instance, not only in pytest: with `time_format:
24h` the journal rendered `20:30, Aug 28` and `08:05, Aug 28` in the browser,
the Health tab read `Last: 37.2 C · 21:42, Aug 28`, the notification was
`🍼 Pump (left) at 21:42`, and the AI summary card read `generated 14:53`. On a
default install the same events rendered `8:30 PM, Aug 28` and
`🍼 Feed at 8:30 PM`.

Two deliberate mutations confirmed the tests are not vacuous: removing the 24h
zero-pad failed 3 tests, and dropping `self.time_fmt` from the `db.py` call
sites failed both the journal unit test and the end-to-end one.

## 8. Acceptance criteria

- With `time_format: 24h`, an event logged at half past eight in the evening
  shows as `20:30, Aug 28` in the journal, and its Home Assistant notification
  reads `🍼 Feed at 20:30`.
- With `time_format: 24h`, a pump reminder, the AI summary card's "generated"
  line, `baby/summary`'s `time` field and
  `sensor.baby_contraction_assessment_time` are all 24h. No surface is left on
  AM/PM.
- With `time_format: 12h`, or the option absent, every string is byte-identical
  to what shipped before this change.
- A garbage `time_format` value falls back to `12h` and does not raise.
- Midnight renders `00:30` in 24h and `12:30 AM` in 12h. Noon renders `12:00` and
  `12:00 PM`.
- The date half of a journal timestamp is unchanged by the setting.
- The Health and Growth tabs follow the setting, not just the journal.
- No `web/i18n/*.json` file changes, and `test_i18n.py`'s completeness test stays
  green with no translation round trip.
- The `datetime-local` pickers still round-trip a backfilled time correctly under
  both settings.
- Existing 98 tests stay green.
