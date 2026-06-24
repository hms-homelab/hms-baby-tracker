# Changelog

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
