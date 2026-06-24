# Baby Tracker: Home Assistant Add-on (+ ESP32 MQTT remote)

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-%23FFDD00.svg?logo=buy-me-a-coffee)](https://www.buymeacoffee.com/aamat09)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on%20%2F%20App-blue.svg?logo=home-assistant)](https://www.home-assistant.io/)
![status](https://img.shields.io/badge/status-active-brightgreen)

A self-contained Home Assistant app/add-on for newborn care tracking: feeds,
diapers, sleep, pumping, baths, medicine, tummy time, weight and notes. You get a
one-tap Ingress web UI, local storage, pump and feed reminders, and native Home
Assistant entities. No n8n, no external database. It pairs with the ESP32 button
remote from the [baby-tracker-suite](https://github.com/aamat09/baby-tracker-suite).

<p align="center">
  <img src="images/dashboard.png" alt="Baby Tracker Ingress web UI with summary stats and one-tap event buttons" width="46%">
  <img src="images/addon-info.png" alt="Baby Tracker running as a native Home Assistant add-on" width="46%">
</p>
<p align="center"><em>One-tap Ingress web UI (left), running as a native Home Assistant add-on (right) with Ingress, MQTT discovery, start-on-boot and ~1% RAM.</em></p>

> **Want the physical button remote?** The 3D-printed ESP32 remote that drives this
> dashboard has build details, photos and a demo on its
> **[project page](https://shmaestro.com/projects/baby-tracker)**. Rather not print
> and solder your own? A pre-built unit is **[available here](https://shop.shmaestro.com/products/baby-tracker)**.
> Either way the add-on works fully standalone.

<p align="center">
  <img src="images/baby-remote.jpg" alt="The 3D-printed ESP32 Baby Remote: a translucent enclosure with 15 labeled buttons and a 0.96-inch OLED" width="320">
</p>
<p align="center"><em>The companion ESP32 Baby Remote: one-tap logging over MQTT, with a 0.96" OLED showing last feed, last pump and the next-pump reminder. Optional, the add-on works on its own.</em></p>


## Features

- **One-tap logging** of 17 event types across 8 categories (feed, pump, diaper,
  sleep, bath, medicine, tummy time, weight, plus regular and special notes).
- **Ingress web UI**: the colorful button dashboard, summary stats and journal,
  served right inside Home Assistant. No extra port to expose, no auth to manage.
- **Native HA entities via MQTT discovery**: `sensor.baby_*`, a `binary_sensor`
  for "currently sleeping", and a `button.*` for each action.
- **Listens to the ESP32 remote** on `baby/remote/event` (and `baby/note`).
- **Fires every logged event on `baby/event`** so you can build your own
  automations. Trigger on the MQTT topic, then notify any phone you like. It
  works for every source (web UI, companion app, or the remote), and the notify
  target picker behaves like any normal automation. See
  [`baby_tracker/DOCS.md`](baby_tracker/DOCS.md).
- **Pump reminders**: per-side timer (default 2h) that sends a Home Assistant
  notification.
- **Feed reminders**: a single timer reset by each breast or bottle feed
  (default 3h).
- **Self-contained**: SQLite under `/data`, survives restarts, with an optional
  external `database_url`.
- **Quiet until you ask for it**: notifications only fire once you set
  `notify_targets` or wire up your own `baby/event` automation.

## Install

1. **Settings → Add-ons** (shown as **Apps** on HA 2026.2+) → **Add-on Store** →
   ⋮ → **Repositories**.
2. Add: `https://github.com/hms-homelab/hms-baby-tracker`
3. Install **Baby Tracker**, set options (at least `timezone`), **Start**, then
   **Open Web UI**.

You'll want an MQTT broker (e.g. the Mosquitto add-on) for the remote and the
native entities. Credentials are auto-discovered through the `mqtt` service. Full
reference: [`baby_tracker/DOCS.md`](baby_tracker/DOCS.md).

## Run standalone (without Home Assistant)

The same app ships as a plain Docker image, so you can run it anywhere with no
Supervisor:

```bash
# app + a Mosquitto broker for the ESP32 remote, data in ./data
docker compose up -d        # -> http://localhost:8099
```

or a single container:

```bash
docker run -d -p 8099:8099 -v "$PWD/data:/data" \
  -e TZ=America/New_York -e MQTT_HOST=192.168.1.10 \
  ghcr.io/hms-homelab/baby-tracker:latest
```

Config is via **env vars** instead of HA options: `TZ`, `PUMP_HOURS`,
`FEED_HOURS`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`,
`DATABASE_URL`, `DATA_DIR`. Point the ESP32 remote's MQTT at the broker and
presses log straight in. (HA `notify` targets only work when running as the
add-on; `baby/event` still fires for your own MQTT automations.) Images are
multi-arch (amd64 + arm64) and published on tagged releases.

## Architecture

```
ESP32-C3 remote ─MQTT─┐
HA UI (Ingress) ──────┤
REST POST /api/event ─┼─▶ Baby Tracker (Docker, /data SQLite)
                      │     ├─ FastAPI: ingest + stats engine + journal
                      │     ├─ APScheduler: pump + feed reminders
                      │     └─ MQTT: discovery + state + baby/event
                      ▼
        HA entities + your automations + phone notifications
```

## Options

> **MQTT auto-configures** with the Mosquitto add-on; set `mqtt_host` as a fallback for an external broker.

| Option | Type | Default | Description |
|---|---|---|---|
| `timezone` | string | `America/New_York` | IANA TZ for "today" rollover and log timestamps |
| `pump_hours` | float | `2` | Hours after a pump event before the reminder fires |
| `feed_hours` | float | `3` | Hours after the last breast/bottle feed before the feed reminder fires |
| `notify_targets` | list | `[]` | HA `notify` service names (without `notify.`) for alerts |
| `mqtt_host` | string | `""` | Fallback broker. Blank auto-discovers the Mosquitto/Supervisor broker; set it (e.g. `192.168.1.15`) for an external broker like EMQX |
| `mqtt_port` | port | `1883` | External broker port (fallback only) |
| `mqtt_username` | string | `""` | External broker username, if it requires auth |
| `mqtt_password` | password | `""` | External broker password, if it requires auth |
| `database_url` | string | `""` | Optional external DB; empty uses the built-in SQLite |

## Development

```bash
cd baby_tracker
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt pytest
DATA_DIR=/tmp/baby MQTT_ENABLED=0 ./.venv/bin/uvicorn app.main:app --port 8099
./.venv/bin/python -m pytest -q     # stats parity tests
```

## Related Projects

- [baby-tracker-suite](https://github.com/aamat09/baby-tracker-suite): the full suite (HA dashboards, n8n flows, ESP32 remote hardware/firmware).
- [hms-mm](https://github.com/hms-homelab/hms-mm): dual ESP32-C3 WiFi SD-card bridge.
- [hms-claude-mem](https://github.com/hms-homelab/hms-claude-mem): semantic memory MCP server.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Support

If this project is useful to you, consider buying me a coffee!

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/aamat09)
