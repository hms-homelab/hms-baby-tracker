# Changelog

## Unreleased

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
