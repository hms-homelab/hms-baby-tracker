# Changelog

## 2026.4.4 - 2026-07-05

- **fix: edit-time picker showed the wrong time**
  ([#2](https://github.com/hms-homelab/hms-baby-tracker/issues/2)). The
  date/time pickers now anchor to the add-on's configured `timezone` (exposed
  via `GET /api/config`) instead of the viewing device's, so they match the
  journal times regardless of where you're viewing from or what timezone your
  phone/browser is set to.
- **fix: journal times drop the leading zero** on the hour (`7:42 PM`, not
  `07:42 PM`).

## 2026.4.3 - 2026-07-05

- **feat: AI daily summaries (SDD-003).** A warm, plain-language recap of the
  baby's day, shown in the summary card — an automatic digest each morning plus a
  **Summarize now** button (2/day). It reads all event types and gently flags
  anything that stands out (a longer feed gap, fewer diapers than yesterday, a
  fever).
  - **Privacy:** the model only ever receives an **anonymized digest** (counts,
    sleep, trends, last temp/weight) — never a name, note, or any free text.
  - **Providers:** a hosted proxy (default), your own **Ollama**, or a **Claude /
    Gemini / OpenAI** key — set with `summary_provider` + `summary_api_key`.
  - The instruction is an editable `summary_prompt` (pre-filled); the code always
    appends the digest, so edits change tone, not what's sent.
  - New `sensor.baby_summary` + retained `baby/summary` MQTT topic. A one-time
    in-app notice discloses the hosted default and points to the opt-out.
  - **On by default** via the hosted proxy (`babytracker.shmaestro.com`); the
    prompt only ever carries the anonymized digest. Switch the provider or turn
    it off in Configuration.

## 2026.4.2 - 2026-07-05

- **fix: stale UI after an update.** The Ingress SPA is now served with
  `Cache-Control: no-cache`, so the browser revalidates `index.html` / `app.js` /
  `styles.css` on every load (cheap 304s via ETag). Updating the add-on now shows
  the new UI immediately — no more clearing the browser/app cache by hand.

## 2026.4.1 - 2026-07-05

The Health and Growth tabs go live (SDD-002, phase 2), completing the six-tab
set.

- **feat: Growth tab.** Log **weight**, **length**, and **head circumference**
  over time; each metric shows the latest value, the **delta since the previous
  reading**, and a tiny inline sparkline of the trend. Backed by new numeric
  `value`/`value_unit` columns on `baby_events` and a `GET /api/growth` endpoint.

- **feat: Health tab.** Log a **temperature** (°C or °F) — flagged as a **fever**
  when it's at/above `fever_threshold_c` (default 38.0) — plus free-text
  **symptom** notes and **medicine** doses (with a "last dose / N today"
  readout). New `temperature` and `symptom` event types.

- **feat: units + measurement system.** New `measurement_system` option
  (`imperial` default, or `metric`) sets the default unit pickers: temperature
  °F/°C, weight **lb + oz** or kg, length/head in or cm. Weight in `lb` uses a
  dedicated lb + oz entry and shows deltas in ounces.

- **feat: one shared note bar.** The per-tab note inputs are consolidated into a
  single always-visible note bar (with the ⭐ special toggle) below the tabs, so
  every tab — including Health — can jot a note.

- **feat: summary dashboard.** The summary card now rolls up every tab —
  contractions today (and in the last 2h), Get Ready progress (done/total), the
  latest temperature and weight — plus a **notifications strip** that surfaces
  active alerts (fever, low-stock and refill-due supplies) in one place instead
  of only inside each tab.

- **feat: unified `baby/alert` MQTT bus + new sensors.** All actionable alerts
  (fever, supply low/refill-due, feed/pump reminders) now publish to one
  `baby/alert` topic with a `kind` field, so an HA automation can subscribe once.
  A live temperature at/above the fever threshold fires a server-side alert. New
  discovery sensors: **Contractions Today**, **Get Ready** (done/total), **Low
  Supplies**. `baby/supply/reminder` stays as a legacy alias.

- **feat: numeric readings.** `POST /api/event` accepts `value` + `value_unit`;
  they flow into the event's title/message, the journal, and the growth series.
  Additive `value`/`value_unit` columns are migrated onto existing `baby_events`
  archives (guarded `ALTER TABLE ADD COLUMN`, SQLite + Postgres).

- **fix: diaper "Change" icon** is now 🩲 (was a placeholder 🔄).

## 2026.4.0 - 2026-07-05

Tabbed redesign + Supplies + a Get Ready checklist (SDD-002, phase 1). The web
UI now has a pinned summary on top, a tab bar, and a pinned journal below that
still logs everything across tabs.

- **feat: tabs.** The Ingress UI is organized into **Get Ready**, **Baby**,
  **Contractions**, and **Supplies** tabs (Health + Growth land in 2026.5.0).
  The tab the app opens on is a new **`default_tab`** option (default `baby`) —
  set it to `contractions` or `get_ready` to lead with the pre-birth phase, then
  switch to `baby` after the arrival. `GET /api/config` exposes it to the UI.

- **feat: Get Ready checklist.** An open, editable prep list seeded with popular
  suggestions (crib, diaper bag, newborn clothes, bottles, wipes + cream, car
  seat), with a progress readout and a manual **Uncheck all**. Optional daily
  auto-reset via **`checklist_reset_hour`** (0 = off). New `baby_checklist` table
  + `GET/POST/PATCH/DELETE /api/checklist` + `/api/checklist/reset`.

- **feat: Contractions tab.** Three big severity buttons — **Mild** (green),
  **Medium** (orange), **Intense** (red) — plus a note and a contraction
  backfill, and a live "N in last 2h · last X min ago · avg gap" readout. Rides
  on the existing `contraction` event + Ollama assessment; `medium` is a new
  intensity alias for `moderate`.

- **feat: Supplies.** Register consumables (formula, diapers, wipes, creams,
  other) with quantity + brand + type. Each can **auto-count-down** from a
  matching baby event (e.g. a bottle feed decrements formula) and remind you to
  refill by a **low-stock threshold** and/or a **days cadence** (whichever fires
  first). Manual −/＋ and Refill too; refills are logged to the journal. New
  `baby_supplies` table + `/api/supplies` CRUD + `/adjust` + `/refill`, a daily
  reminder sweep at **`supply_reminder_hour`** (default 9), and a
  `baby/supply/reminder` MQTT topic for HA automations.

- **fix: journal row editor.** Opening the inline editor on a row that has a
  note no longer squeezes the label into a one-character-per-line column — the
  editor wraps onto its own full-width line. Tapping an open row now **collapses**
  it (and opening another row closes the first).
- Backfilled/edited past events do not auto-decrement supplies (only live ones).
- New tables auto-create on existing installs; existing events are untouched.

## 2026.3.1 - 2026-07-05

- **feat: distinct breast vs bottle icons.** Answers the follow-up on
  [#1](https://github.com/hms-homelab/hms-baby-tracker/issues/1). Breast feeds now
  show 🤱 and bottle feeds 🍼 across the buttons, journal and manual-entry
  dropdown, so the two read apart at a glance. Pump keys move to 🫙 (matching the
  summary and journal) and the diaper "Change" key to 🔄.

- **feat: attach/edit a note when editing a logged event.** Also from
  [#1](https://github.com/hms-homelab/hms-baby-tracker/issues/1). The inline row
  editor now has a note field, pre-filled with the event's existing note, saved
  alongside the time via `PATCH api/event/{id}`.

## 2026.3.0 - 2026-06-24

- **feat: "nocturnal nursery" dashboard redesign.** The Ingress web UI now matches
  the Baby Remote app exactly: a dark, warm theme with a single amber nightlight
  accent, a per-event colour system, the Bricolage Grotesque / Hanken Grotesk /
  JetBrains Mono type set, a glowing summary hero, dark "remote key" buttons with
  colour-coded icons, and a clean mono-timestamped journal. Same logging,
  backfill and inline-edit behaviour, restyled.

- **feat: edit an event's time, backfill a missed one, or delete it.** Answers
  [#1](https://github.com/hms-homelab/hms-baby-tracker/issues/1). Miss a feed or
  log one late and it no longer skews the timeline:
  - **Add / backfill an event** card in the web UI: pick a type, set a past
    date/time, optional note, then Add.
  - **Tap any journal row** to fix its time or delete the event inline.
  - New REST endpoints: `POST api/event` accepts an optional `logged_at`
    (ISO8601) for backfill; `PATCH api/event/{id}` edits `logged_at` / `note` /
    `event_subtype`; `DELETE api/event/{id}` removes an event. All work on both
    the SQLite and Postgres backends.

  Edits and deletes recompute stats and refresh the device OLED immediately, but
  do not re-fire `baby/event` or send a push (those stay reserved for new events),
  and backfilled past events don't arm a feed/pump reminder. See DOCS, "Editing
  and backfilling events".

## 2026.2.3

- **feat: publish every stored event on MQTT (`baby/event`)** — in addition to
  writing the DB (and the optional `notify_targets` push), each event is now
  re-fired on `baby/event` (non-retained) for ANY source (web UI, app REST, or
  the remote). Build your own HA automation with an MQTT trigger on `baby/event`
  and the notify target picker works normally — fixing the case where selecting
  targets directly didn't notify. Payload carries `event_type`, `event_subtype`,
  `note`, `logged_at`, `title`, `message`, `id`, `source`. Kept separate from the
  inbound `baby/remote/event` topic to avoid a re-ingest loop. See DOCS → "Build
  your own automation (MQTT)".

## 2026.2.2

- **fix: restore phone notifications (Supervisor token re-inject)** — the running
  container had lost its `SUPERVISOR_TOKEN` after manual restarts/rebuilds, so the
  add-on couldn't reach the core notify proxy (`notify_targets set but no
  SUPERVISOR_TOKEN; skipping`). The manifest already requests it
  (`hassio_api`/`homeassistant_api`); a version bump forces the Supervisor to
  recreate the container under its own management and re-inject the token. Also
  added a startup log line reporting whether `SUPERVISOR_TOKEN` is present (token
  value never logged) so this is diagnosable at a glance next time.

## 2026.2.1

- **fix: alert published on change only** — stops the piezo beeping every 60s on
  display refresh. The device firmware chimes on every received
  `baby/remote/alert` "1" (no rising-edge tracking), so re-emitting the retained
  "1" on each 60s display refresh beeped the piezo every minute. The add-on now
  publishes the alert only when it changes, so the chime fires once on the real
  0→1 transition. The OLED display rows (`baby/remote/display`) still refresh
  every 60s, silently.

## 2026.2.0

- **Contraction AI assessment (opt-in)** — ports the n8n "Contraction AI
  Assessment" workflow into the add-on. When `ollama_enabled: true`, each logged
  `contraction` event triggers a local Ollama call (`/api/generate`,
  `gpt-oss:120b-cloud` by default) over the last 2 hours of contractions and
  publishes a 2-sentence labor-stage assessment to the new **Contraction
  Assessment** / **Contraction Assessment Time** sensors (retained
  `baby/assessment`) plus, best-effort, the legacy `input_text.ai_assessment[_time]`
  entities. Off by default; new `ollama_*` options. Stat math and prompt are a
  faithful port of the n8n Code node. This was the last baby workflow on n8n.
- **Drives the Baby Remote's OLED directly** — replaces the n8n "Baby Remote
  Display" flow. A 60 s job (and an instant refresh after every feed/pump)
  publishes the 3 display rows to `baby/remote/display` and the pump-due flag to
  `baby/remote/alert` (both retained), computed from the latest feed/pump using
  `pump_hours` as the due threshold. Payloads are byte-compatible with the n8n
  flow the firmware was built against.
- **Feed reminders now pop a banner on the device** via `baby/remote/reminder`
  (`{"l1","l2","secs":4}`) in addition to the phone notification, matching the
  n8n "Notify Device" node.
- **Contraction events** now have their own icon (⏱️) instead of falling back to
  📝, in the UI, MQTT discovery and history replay.
- With these, the add-on is a full standalone replacement for the n8n baby
  workflows (event/note logging, display, reminders, stats, history replay) — no
  n8n dependency.

## 2026.1.3

- **Feed reminders.** Each breast/bottle feed (re)arms a single timer; when it
  fires (default `feed_hours: 3`) a "🍼 Feed Reminder" notification goes to your
  `notify_targets`, mirroring the existing pump reminder. Any newer feed resets
  the clock. New `feed_hours` option (env `FEED_HOURS` for the standalone image).

## 2026.1.2

- Sleep is now logged as two explicit actions: **Sleep Start** and **Sleep End**
  (two MQTT buttons + two tiles in the web UI) instead of a single auto-toggling
  Sleep button. The ESP32 remote maps this onto one physical key: a single tap
  is sleep start, a double tap (two presses under ~1s apart) is sleep end. The
  backend still accepts a missing/`toggle` subtype and auto-derives start/end, so
  un-reflashed remotes keep working.

## 2026.1.1

- MQTT broker resolution is now **auto-first**: the Supervisor-provided broker
  (Mosquitto add-on) is used automatically with zero config; the `mqtt_host`
  option is a **fallback** for an external broker (e.g. EMQX). Previously the
  explicit option took precedence over auto-discovery.
- Pre-built ghcr images: installs now **pull** instead of building on-device
  (add-on multi-arch + a standalone non-HA image), published on `v*` tags.
- Docs: document the MQTT options and the auto-discover/fallback behavior.

## 2026.1.0

Initial release.

- Ingress web UI for logging and reviewing baby-care events (feeds, diapers,
  sleep, baths, medicine, tummy time, weight, pumping, free-text notes).
- FastAPI backend (`app.main:app`) on port 8099 behind Home Assistant Ingress.
- REST API under `api/` (relative URLs, Ingress-path-aware):
  `GET api/log`, `POST api/event`, `POST api/note`, `POST api/reset`.
- MQTT bridge:
  - Subscribes to `baby/remote/event` (ESP32 button remote + HA buttons) and
    `baby/note`.
  - Publishes retained stats to `baby/state` and availability to `baby/status`.
  - Auto-creates native Home Assistant entities via MQTT discovery (sensors,
    a `Sleeping` binary_sensor, and one button per event type).
- Pump reminders: arms a configurable (`pump_hours`) reminder after each pump.
- Optional phone notifications via `notify_targets` (HA `notify.*` services).
- Configurable timezone; optional external database via `database_url`.
- SQLite persistence in `/data` (survives restarts/updates).
- Multi-arch: aarch64, amd64, armv7, armhf, i386.
