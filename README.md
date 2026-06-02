# hms-baby-tracker

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-%23FFDD00.svg?logo=buy-me-a-coffee)](https://www.buymeacoffee.com/aamat09)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on%20%2F%20App-blue.svg?logo=home-assistant)](https://www.home-assistant.io/)
![status](https://img.shields.io/badge/status-active-brightgreen)

A self-contained **Home Assistant app/add-on** for newborn care tracking — feeds,
diapers, sleep, pumping, baths, medicine, tummy time, weight and notes — with a
one-tap Ingress web UI, local storage, pump reminders, and native HA entities.
No n8n, no external database. Pairs with the ESP32 button remote from the
[baby-tracker-suite](https://github.com/aamat09/baby-tracker-suite).

<p align="center">
  <img src="images/dashboard.png" alt="Baby Tracker dashboard — summary stats and one-tap event buttons" width="340">
</p>

## Features

- **One-tap logging** of 17 event types across 8 categories (feed/pump/diaper/
  sleep/bath/medicine/tummy time/weight + regular & special notes)
- **Ingress web UI** — the colorful button dashboard, summary stats, and journal,
  served right inside Home Assistant (no extra port, no auth to manage)
- **Native HA entities via MQTT discovery** — `sensor.baby_*`, a
  `binary_sensor` for "currently sleeping", and a `button.*` per action
- **Listens to the ESP32 remote** on `baby/remote/event` (and `baby/note`)
- **Pump reminders** — per-side timer (default 2h) → HA notifications
- **Self-contained** — SQLite under `/data`; survives restarts; optional external
  `database_url`
- **Silent until configured** — notifications only fire once you set
  `notify_targets`

## Install

1. **Settings → Add-ons** (shown as **Apps** on HA 2026.2+) → **Add-on Store** →
   ⋮ → **Repositories**.
2. Add: `https://github.com/hms-homelab/hms-baby-tracker`
3. Install **Baby Tracker**, set options (at least `timezone`), **Start**, then
   **Open Web UI**.

Requires an MQTT broker (e.g. the Mosquitto add-on) for the remote + native
entities; credentials are auto-discovered via the `mqtt` service. Full reference:
[`baby_tracker/DOCS.md`](baby_tracker/DOCS.md).

## Architecture

```
ESP32-C3 remote ─MQTT─┐
HA UI (Ingress) ──────┤
REST POST /api/event ─┼─▶ Baby Tracker (Docker, /data SQLite)
                      │     ├─ FastAPI: ingest + stats engine + journal
                      │     ├─ APScheduler: pump reminders
                      │     └─ MQTT: discovery + state ─▶ HA sensors/buttons + notify
                      ▼
              HA entities + phone notifications
```

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `timezone` | string | `America/New_York` | IANA TZ for "today" rollover + log timestamps |
| `pump_hours` | float | `2` | Hours after a pump event before the reminder fires |
| `notify_targets` | list | `[]` | HA `notify` service names (without `notify.`) for alerts |
| `database_url` | string | `""` | Optional external DB; empty = built-in SQLite |

## Development

```bash
cd baby_tracker
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt pytest
DATA_DIR=/tmp/baby MQTT_ENABLED=0 ./.venv/bin/uvicorn app.main:app --port 8099
./.venv/bin/python -m pytest -q     # stats parity tests
```

## Related Projects

- [baby-tracker-suite](https://github.com/aamat09/baby-tracker-suite) — the full suite (HA dashboards, n8n flows, ESP32 remote hardware/firmware)
- [hms-mm](https://github.com/hms-homelab/hms-mm) — dual ESP32-C3 WiFi SD-card bridge
- [hms-claude-mem](https://github.com/hms-homelab/hms-claude-mem) — semantic memory MCP server

## License

MIT License — see [LICENSE](LICENSE) for details.

## Support

If this project is useful to you, consider buying me a coffee!

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/aamat09)
