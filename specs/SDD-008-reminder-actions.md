# SDD-008: Reminder series defects (amends SDD-007)

Status: IMPLEMENTED, shipped in 2026.5.2
Amends: SDD-007 — these are bugs in that feature, not a new one
Origin: the same 6-hourly Tylenol series, one night of living with it
Baseline: 2026.5.1

## 1. Problem

SDD-007 delivers the alert. It stops there, and two defects show up the first
night you actually use it.

**The alert is a dead end.** It says "Tylenol For Mikey 8ml, every 6h" and
nothing more. Acting on it means unlocking the phone, opening Home Assistant,
finding the add-on, finding the row, and logging the dose by hand — at 3am, with
a baby in the other arm. The notification knows exactly which series called, and
throws that away.

**The interval lies.** A series fires on the grid it was armed on. Arm "every 6h"
at 20:00 and it fires at 02:00, 08:00, 14:00 forever, whatever time the dose is
actually given. Wake up twenty minutes late and the next alert is 5h40m after the
dose. Do that three nights running and the gap has quietly walked in by an hour.
For a medicine reminder the number on the screen has to mean what a parent reads
it to mean: six hours from the dose that was given.

## 2. Non-goals

- **No auto-logging.** A reminder firing is not evidence a dose was given. The
  journal must never claim one because a timer went off and everyone slept
  through it. A record appears when a person says it did.
- **No medical logic.** Unchanged from SDD-007. Re-anchoring makes the interval
  honest; it does not make it safe. No dose caps, no minimum spacing, no warning.
- **No new notification transport.** Still `baby/alert`, still an HA automation.
- **No matching heuristics.** A dose logged from the journal does not re-anchor
  anything (see 3.2) — the add-on does not guess which series a row belongs to.

## 3. Design

### 3.1 The link

`_fire_series` asks an optional resolver for a URL and puts it on the alert as
`url`:

```
/hassio/ingress/<slug>#reminder=<id>
```

`main.py` supplies the resolver from the Supervisor slug it already fetches for
the Configuration link; standalone there is no Supervisor and no HA to open, so
the resolver returns nothing and the alert simply carries no `url`. The web UI
reads `#reminder=<id>` after the Reminders card renders, scrolls that row into
view and marks it for a few seconds, then clears the fragment so a later refresh
does not keep re-highlighting a series already dealt with.

### 3.2 Re-anchoring, and why the record carries the series id

`baby_events` gains a nullable `reminder_id` (additive on both backends, in
`EXPORT_TABLES`). A row carrying it means "this is the dose that series asked
for". The ingest funnel, seeing one on a live event, calls `reanchor_series`:
`start_at` moves to now and the job is rebuilt, so the next fire is a full
interval from the record.

The tag is the whole point. The obvious alternative — re-anchor any series whose
`event_type`/`event_subtype` matches the new row — breaks the exact case that
motivated SDD-007: a Tylenol series and an antibiotic series are both `medicine`,
so either dose would reset both. Tagging keeps them independent, at the cost that
a dose logged from the journal re-anchors nothing. That is the deliberate trade:
silently resetting the wrong medicine's countdown is worse than not resetting a
countdown at all.

Daily series are a wall-clock time, so there is nothing to move: `reanchor_series`
leaves them alone. Backfilled events (`logged_at` set) never re-anchor either — a
dose typed in after the fact must not drag a live countdown.

### 3.3 Getting the tap back into the add-on

The notification's buttons fire an HA event, which an automation turns into MQTT:

| topic | payload |
| --- | --- |
| `baby/reminder/action` | `{"action": "log"｜"snooze", "reminder_id": N, "minutes": M}` |

MQTT rather than REST because Ingress needs a session a notification action has
no way to hold, and the broker is already the path every other device uses to log
into this add-on.

`log` runs the same code as `POST api/reminders/{id}/log`: read the series, write
an event from its `event_type` / `event_subtype` / `title`, tagged with the id —
which re-anchors through the ordinary funnel. One path, whether the dose is
logged from the phone's button, the card's **Log it**, or the REST endpoint.

`snooze` moves only the pending fire (default 15 minutes) and touches neither the
row nor `fire_count`. A snooze is not a dose.

### 3.4 Home Assistant

`kind: reminder` is split out of the generic baby notifier into its own
automation, so only reminders carry actions and nothing double-notifies. It sends
`url`, `action_data.reminder_id`, and two actions (`BABY_REMINDER_LOG`,
`BABY_REMINDER_SNOOZE`) at `time-sensitive` interruption level. A second
automation maps the returned action to the MQTT payload above, reading the id
from either the flattened event data or `action_data`.

## 4. Surface

- `POST api/reminders/{id}/log` -> `{ok, event, next_run}`, 404 on a missing series
- `POST api/reminders/{id}/snooze?minutes=15` -> `{ok, next_run}`
- MQTT in: `baby/reminder/action`
- Alert payload gains `url`
- `baby_events.reminder_id`
- Web: **Log it** on each armed series; `#reminder=<id>` deep link
- i18n: `rem.log`, `status.doseLogged` (en/es/fr/nl)

## 5. Tests

`tests/test_reminders.py` covers: logging re-anchors to now; the record is tagged,
noted and lands in the journal; a second series is untouched; a plain `medicine`
log re-anchors nothing; a daily series is not dragged; snooze moves the fire
without recording one; 404s; the alert carries the deep link when a resolver
exists and fires fine without one; `reminder_id` survives export.
