# SDD-002 — Tabbed UI: Get Ready / Baby / Contractions / Health / Growth / Supplies

Status: **Phase 1 SHIPPED (2026.4.0), Phase 2 IMPLEMENTED** — 2026-07-05 (Health + Growth + units + shared notes + summary dashboard + unified baby/alert bus; shipping as a patch **2026.4.1**, not 2026.5.0, to stay on the 4.0 line)
Date: 2026-07-05
Component: `baby_tracker/` (web SPA + `app/`)
Ships as: **2026.4.0** then **2026.5.0** (minors — new features, backward-compatible)

## 1. Goal

Reorganize the Ingress SPA into **six tabs** between the pinned summary card and
the pinned journal, following the parenting journey:

- **Get Ready** — an open prep checklist for mom, seeded with popular
  suggestions (crib, diaper bag, newborn clothes, bottles…) and fully editable;
  progress readout + manual reset (optional daily auto-reset).
- **Baby** — the current logging surface (feed/pump/diaper/other, notes,
  add/backfill). Unchanged behavior, moved into a tab.
- **Contractions** — three big severity buttons (Mild / Medium / Intense) sized
  to the card, plus a note field and a contraction backfill. Rides on the
  **existing** `contraction` event type + intensity model + Ollama assessment.
- **Health** — temperature (numeric, fever flag), symptom notes, medicine doses
  (reuses the existing `medicine` event).
- **Growth** — weight / length / head-circumference over time with a simple
  trend readout (delta since last + tiny sparkline).
- **Supplies** — register consumables (formula, diapers, wipes, creams, other)
  with quantity + brand + type, **auto-decrement** from matching baby events, and
  **refill reminders** by low-stock threshold and/or a days cadence.

The **summary card stays pinned on top** and the **journal stays below the tabs
and logs everything** (all tabs' events land in one timeline).

The **default tab is a configurable add-on option** (`default_tab`, default
`baby`). Rationale from the journey: pre-birth a mom wants **Contractions** (or
**Get Ready** for the hospital bag) front-and-center without the baby UI in the
way; after birth flip the default back to **Baby**.

## 2. Background — what already exists

- **Contractions are already wired.** `app/ingest.py` has `contraction: ⏱️`;
  `app/assessment.py` resolves intensity from `event_subtype`/`note` against
  `_INTENSITY_MAP = {mild:1, moderate:2, strong:3, intense:4}` and runs an
  opt-in Ollama labor assessment on each `contraction`, publishing
  `sensor.baby_contraction_assessment(_time)` via MQTT discovery. The
  Contractions tab is **mostly a UI surface** over existing plumbing.
- **`medicine` and `weight` event types already exist** — Health reuses
  `medicine`; Growth extends `weight` with `length` + `head_circumference`.
- **Every stored event fires `baby/event`** (non-retained) through the single
  `ingest_and_broadcast` funnel in `app/main.py` (store → arm reminder → publish
  MQTT → notify). Supplies auto-decrement hooks into that same funnel.
- **Reminders** = `app/scheduler.py` (APScheduler) + `app/notify.py` (HA notify
  via Supervisor proxy) + `app/mqtt.py`. Supply/appointment/checklist jobs reuse
  this.
- Data model today: `baby_events(id, event_type, event_subtype, note,
  logged_at)`, dual backend (SQLite `/data` + optional Postgres) in `app/db.py`,
  `CREATE TABLE IF NOT EXISTS` on startup.

## 3. Design

### 3.1 Layout & tabs

```
┌ Summary card (pinned) ─────────────────────────────────────────┐
├ Tab bar (horizontally scrollable icon+label chips):            │
│  [🎒 Get Ready][👶 Baby][⏱ Contractions][🌡 Health][📈 Growth][🧴 Supplies]
│    …selected panel renders here…                               │
├ Journal (pinned below — logs ALL event types in one timeline) ─┤
└ Reset All footer ──────────────────────────────────────────────┘
```

- Pure CSS/JS tabs (show/hide panels), no framework — keeps the vanilla-JS SPA.
- Tab bar is **horizontally scrollable** on narrow phones (6 chips won't fit);
  short labels + emoji.
- Initial tab = `default_tab` option (source of truth so the install default is
  changeable); `localStorage` remembers the last tab tapped within a browser for
  convenience.

### 3.2 Schema changes (dual backend, additive/safe)

1. **`baby_events` gains numeric value columns** (guarded `ALTER TABLE ADD
   COLUMN`, `IF NOT EXISTS` on SQLite via pragma check / `IF NOT EXISTS` on PG):
   `value REAL NULL`, `value_unit TEXT NULL`. Used by temperature/weight/length/
   head-circumference so Growth/Health can render trends. Non-numeric events
   leave them null — fully backward-compatible.
2. **`baby_supplies`** (new) — see §3.7.
3. **`baby_checklist`** (new) — see §3.3.

### 3.3 Get Ready tab (new — `baby_checklist`)

`baby_checklist(id, label, position, done INT, done_at TEXT, updated_at TEXT)`.

A prep checklist that spans the journey — big one-time arrival prep
(nursery / hospital bag) *and* ad-hoc daily prep.

- Editable, open-ended: add / rename / reorder / delete items; tap to
  check/uncheck. Parents add whatever they want.
- Progress readout ("3 / 6 ready") + a manual **Reset** (uncheck all) button.
- Seeded defaults on first run (popular suggestions, all editable/removable):
  **Crib**, **Diaper bag**, **Newborn clothes**, **Bottles**, **Wipes + cream**,
  **Car seat installed**.
- Optional daily auto-reset: `checklist_reset_hour` (default `0` = off, since the
  seeded items are mostly one-time prep). Set a local hour to auto-uncheck each
  morning if used as a daily list.
- API: `GET /api/checklist`, `POST /api/checklist` (add), `PATCH
  /api/checklist/{id}` (label/done/position), `DELETE /api/checklist/{id}`,
  `POST /api/checklist/reset`.
- Not journaled (it's checklist state, not a timeline event).

### 3.4 Contractions tab (minimal backend change)

- Three big buttons → log a `contraction` event with `event_subtype`:
  **Mild** (green), **Medium** (orange), **Intense** (red), full-card-width tall
  tiles.
- Add `"medium": 2` alias to `_INTENSITY_MAP` (keep `moderate`; `strong` stays
  for legacy/AI values).
- Note field → note on the next/standalone contraction. Backfill → datetime +
  intensity via `POST /api/event` with `logged_at`.
- Readout → current Ollama assessment text if present (else count + last gap, or
  "Need 2+ contractions in 2h"). Assessment stays opt-in.
- These are `baby_events` → already appear in the journal + fire `baby/event`.

### 3.5 Health tab

- **Temperature**: numeric input + unit (°C default) → `temperature` event with
  `value`/`value_unit`. Flag fever when `value ≥ fever_threshold_c` (option,
  default 38.0) — visual badge + optional notify.
- **Symptom note**: `symptom` event (🤒) with free text.
- **Medicine dose**: reuses the existing `medicine` event (log a dose, optional
  name/note). Shows last dose + count today.
- Icons: temperature 🌡, symptom 🤒 (medicine 💊 exists).
- (Stretch, gate on time) simple **appointment**: `appointment` event with a
  future `logged_at` + reminder N hours before; otherwise deferred to a later
  release.

### 3.6 Growth tab

- Three numeric loggers → events with `value`/`value_unit`:
  **Weight** (⚖ existing), **Length** (📏 new `length`), **Head circumference**
  (🧢 new `head_circumference`).
- Each shows the latest value, **delta since previous**, and a tiny inline
  sparkline of the last ~6 entries (SVG, no lib).
- Reads history from `GET /api/growth` (or extend `/api/log` with a filtered
  query) grouped by metric.

### 3.7 Supplies tab (new — `baby_supplies`)

`baby_supplies(id, category, name, brand, type, quantity REAL, unit,
low_threshold REAL NULL, refill_days INT NULL, last_refill_at, consume_event_type
NULL, consume_event_subtype NULL, consume_amount REAL DEFAULT 1, created_at,
updated_at)`. `category` ∈ formula/diapers/wipes/cream/other.

- **Auto-decrement (chosen: on).** In `ingest_and_broadcast`, after a **live**
  event is stored, `supplies.apply_consumption(db, type, subtype)` subtracts
  `consume_amount` from each supply whose `consume_event_type` matches (subtype
  match or null), clamps at 0, and on crossing `low_threshold` fires a **one-shot
  debounced** low reminder (won't re-fire until restocked above threshold).
  Backfilled/edited past events do **not** consume (v1 — matches how backfill
  skips reminders today). Presets in the add form: Formula→feed/bottle×1,
  Diapers→diaper×1, Wipes→diaper×~2, Creams→manual.
- **Reminders (chosen: both).** Daily `check_supplies` job at
  `supply_reminder_hour` (default 09:00 local) reminds for any supply that is
  **low** (`quantity ≤ low_threshold`) or **due** (`now ≥ last_refill_at +
  refill_days`). Immediate fire on decrement threshold-cross too. Debounced per
  supply.
- **API**: `GET /api/supplies` (with `is_low`/`is_due`), `POST /api/supplies`,
  `PATCH /api/supplies/{id}`, `POST /api/supplies/{id}/adjust {delta|set}`,
  `POST /api/supplies/{id}/refill {quantity?}` (restock, reset `last_refill_at`,
  clear debounce, **log a `supply` refill event** → journal), `DELETE
  /api/supplies/{id}`.
- **MQTT**: reminders on `baby/supply/reminder` (non-retained). Refill events
  fire `baby/event`. (Phase 2 if time: `sensor.baby_supply_<slug>` +
  `binary_sensor.*_low` discovery.)

### 3.8 Config options (`config.yaml` options/schema)

- `default_tab`: enum get_ready|baby|contractions|health|growth|supplies,
  default `baby`.
- `supply_reminder_hour`: int, default 9.
- `checklist_reset_hour`: int, default 0 (0 = off; set a local hour for daily
  auto-uncheck of the Get Ready list).
- `fever_threshold_c`: float, default 38.0.

## 4. Decisions (resolved 2026-07-05)

1. **Refill reminder trigger** → **Both** (low-stock threshold and/or days
   cadence, whichever fires first). ✅
2. **Consumption** → **Auto-decrement** via per-supply consume rule; manual +/−
   still available. ✅
3. **Default tab** → **configurable** (`default_tab`, default `baby`), switchable
   to `contractions`/`get_ready` for the pre-birth phase. ✅
4. **Contraction severities** → 3 buttons Mild/Medium/Intense; add `medium`
   alias, keep the 4-level map. ✅
5. **Tab set** → 6 tabs: Get Ready, Baby, Contractions, Health, Growth,
   Supplies. ✅

### 4.1 open follow-ups (call out, don't block)
- Backfilled/edited events auto-decrement? → **No** for v1.
- Health appointments + per-supply HA discovery sensors → stretch, gate on time.

## 5. Out of scope (v1)
- Multi-baby, growth percentile curves (just raw trend), barcode scan, photos,
  serving-size math beyond a flat per-event `consume_amount`.

## 6. Acceptance / test plan
- **Tabs:** 6 tabs switch; summary pinned on top, journal pinned below shows all
  event types together; `default_tab` honored on load and switchable.
- **Get Ready:** seeded with the popular defaults (crib, diaper bag, newborn
  clothes, bottles, wipes + cream, car seat); add/rename/check/reorder/delete;
  progress updates; manual Reset unchecks all; optional daily auto-reset fires
  when `checklist_reset_hour` is set (test with a short interval).
- **Contractions:** Mild/Medium/Intense log `contraction` w/ subtype → journal →
  assessment (medium→2).
- **Health:** temperature logs with numeric value + fever flag ≥ threshold;
  symptom + medicine log and appear in journal.
- **Growth:** weight/length/head log numeric values; delta + sparkline render
  from history.
- **Supplies:** CRUD; manual +/−; auto-decrement clamps at 0; low reminder fires
  on cross and doesn't re-fire until restocked; cadence reminder fires when due;
  refill logs a journal row + `baby/event`.
- **MQTT:** `baby/supply/reminder` fires; `baby/event` still fires.
- **Migration:** `value`/`value_unit` columns + `baby_supplies` + `baby_checklist`
  auto-add on existing installs; existing `baby_events` untouched; survives
  restart. Both SQLite + Postgres.

## 7. Versioning & phasing
`VERSION` + CHANGELOG → **2026.4.0**. Standard two-phase release: tag `v2026.4.0`
(CI publishes ghcr images) → then bump `config.yaml` version. Tag on request only.

**Phasing (APPROVED 2026-07-05):**
- **2026.4.0 (Phase 1)** — tab shell + config `default_tab` + Get Ready +
  Contractions + Supplies (the core reorg + the two headline features).
- **2026.5.0 (Phase 2)** — Health + Growth (needs the `value`/`value_unit`
  migration and trend rendering).

The `baby_events` `value`/`value_unit` migration (§3.2.1) lands in Phase 2 with
Health/Growth; Phase 1 needs only `baby_supplies` + `baby_checklist`.
