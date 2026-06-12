# Changelog

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
