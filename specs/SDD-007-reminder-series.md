# SDD-007: Reminder series (repeat any logged row on a schedule)

Status: IMPLEMENTED, shipped in 2026.5.0
Origin: a sick baby and a 6-hourly Tylenol dose
Baseline: 2026.4.16

## 1. Problem

Every reminder the add-on can send is hardcoded: pump (per side), feed, supply
low/refill-due. There is no way to say "remind me to do THIS again, every N
hours, until I say stop".

The case that forced it: a dose of infant Tylenol every 6 hours through the
night, then an antibiotic twice a day for ten days. Both are already logged as
journal rows (`medicine`), and the useful action is exactly "do that again on a
clock". Today that means an external timer app, on the phone of whoever set it,
with no record next to the dose it belongs to.

## 2. Non-goals

- **No new notification transport.** A series fires on the existing `baby/alert`
  bus, so the HA automation that already notifies phones for pump/feed/supply
  reminders picks it up with no changes.
- **No medical logic.** The add-on does not know a safe dosing interval, does not
  cap doses per day, and does not warn about one. It repeats what the user asked
  for, and nothing else.
- **No per-parent routing.** One household, one alert bus; who gets the push is
  the HA automation's business, as it already is.
- **No weekly / cron-expression schedules.** Two shapes cover the cases that
  motivated this; a third can be added later without changing the row.

## 3. Design

### 3.1 Data

New table `baby_reminders` (both backends, additive `CREATE TABLE IF NOT
EXISTS`, and part of `EXPORT_TABLES` so it is backed up and restored):

| column | meaning |
| --- | --- |
| `title` | what to remind about ("Tylenol"); prefilled from the row's note |
| `event_type` / `event_subtype` | the journal row it was armed from (informational) |
| `mode` | `interval` or `daily` |
| `interval_min` | interval mode: minutes between fires |
| `at_time` | daily mode: `HH:MM` in the add-on's timezone |
| `start_at` | when the clock started (creation, or the last schedule edit) |
| `stop_at` | optional ISO end; `NULL` = until stopped by hand |
| `enabled` | paused series stay in the list, unarmed |
| `last_fired_at` / `fire_count` | run record, shown as a badge |

The DB row is the source of truth. The APScheduler job is a rebuildable mirror
of it: every write re-arms the job, and `load_series()` re-arms all of them on
startup (jobs live in memory, so a restart would otherwise silently drop them).

### 3.2 Scheduling

`Reminders._series_trigger(row)` builds an `IntervalTrigger` or a `CronTrigger`:

- **First fire is one full interval out.** Arming "every 6h" at 2pm first fires
  at 8pm. Firing on save would be noise, and the dose that prompted the series
  was just given.
- **Catch-up, not burst.** A series armed before a long outage rolls its start
  forward to the next slot in the future, so a 25-hour gap on an every-6h series
  produces one upcoming fire, not four missed ones at once. Within the hour,
  `misfire_grace_time=3600` + `coalesce` still deliver a run missed across a
  restart.
- **`end_date=stop_at`** retires the series; a fire that lands past the stop date
  publishes nothing and flips `enabled` to 0.
- A schedule edit (`mode`, `interval_min`, `at_time`) or a resume resets
  `start_at` to now: changing "every 6h" to "every 4h" means 4h from this
  moment, not from the dose that started it.

### 3.3 Firing

`_fire_series` re-reads the row before publishing, so an edited title or a
series stopped on another device takes effect without a restart. It publishes on
`baby/alert`:

```json
{"kind": "reminder", "title": "⏰ Tylenol", "message": "Tylenol · every 6h",
 "reminder_id": 3, "reminder_title": "Tylenol",
 "event_type": "medicine", "event_subtype": null}
```

`kind` is new; the existing kinds are untouched, so an automation that branches
on `feed_reminder` / `pump_reminder` / `supply_low` keeps working and a
catch-all automation starts delivering these for free.

### 3.4 REST

- `GET /api/reminders` — every series, each with a live `next_run`
- `POST /api/reminders` — create **and arm immediately** (no restart, no poll)
- `PATCH /api/reminders/{id}` — retitle, reschedule, pause/resume
- `DELETE /api/reminders/{id}` — stop and remove

Validation rejects `interval_min < 1`, a malformed `at_time`, an empty title,
and any mode other than the two.

### 3.5 UI

- The journal row's inline editor gains **Remind me**, which opens a compact form
  in place: title (prefilled with the row's note, else its label), `Every [N]
  [hours|minutes]` or `Every day at [HH:MM]`, and an optional stop date.
- A pinned **Reminders** card lists the armed series with the next fire, the
  schedule, the run count, and Pause / Stop. It stays hidden until a series
  exists, and can be hidden outright with `hidden_modules: [card.reminders]`.

## 4. Testing

`tests/test_reminders.py` (17 tests): the humanizer and ISO parsing, DB CRUD,
backup round-trip, first-fire timing, outage catch-up, cron construction,
rejection of invalid/expired series, the alert payload, no-op fires for a
paused/deleted/expired series, all four endpoints, and a restart re-arming the
stored series.
