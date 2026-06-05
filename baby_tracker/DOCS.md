# Baby Tracker

Track feeds, diapers, sleep, pumping, baths, medicine, tummy time, weight and
free-text notes for your baby — from a phone-friendly web UI inside Home
Assistant, from an ESP32 button remote over MQTT, or from Home Assistant
automations. Stats are exposed back to Home Assistant as native entities.

## Installation

1. In Home Assistant, go to **Settings → Add-ons** (called **Apps** in the UI
   from HA 2026.2 onward) → **Add-on Store**.
2. Click the **⋮** menu (top-right) → **Repositories**.
3. Add the repository URL:

   ```
   https://github.com/hms-homelab/hms-baby-tracker
   ```

4. Close the dialog, find **Baby Tracker** in the store, and click **Install**.
5. After it installs, open the **Configuration** tab, set your options (at least
   `timezone`), then **Save** and **Start** the add-on.
6. Click **Open Web UI** (Ingress) to launch the tracker. You can also enable
   **Show in sidebar** so it appears as a panel.

> The MQTT integration is a dependency (`mqtt:need`). Install/start the
> Mosquitto broker add-on (or another MQTT broker) first so credentials can be
> auto-discovered — no manual MQTT setup is needed in this add-on.

## Configuration

| Option           | Type            | Default            | Description                                                                                 |
| ---------------- | --------------- | ------------------ | ------------------------------------------------------------------------------------------- |
| `timezone`       | string          | `America/New_York` | IANA timezone used for "today" rollover and the formatted timestamps in the log.            |
| `pump_hours`     | float           | `2`                | Hours after a pump event before a pump reminder is fired.                                   |
| `notify_targets` | list of strings | `[]`               | Home Assistant `notify` service names (without the `notify.` prefix) to send alerts to.     |
| `database_url`   | string (opt.)   | `""`               | Optional external database URL. Leave empty to use the built-in SQLite store under `/data`. |
| `mqtt_host`      | string (opt.)   | `""`               | MQTT broker host. **Leave blank to auto-discover the Mosquitto add-on**; set it (e.g. `192.168.1.15`) to point at an **external broker** like EMQX on another host. |
| `mqtt_port`      | port            | `1883`             | MQTT broker port. |
| `mqtt_username`  | string (opt.)   | `""`               | MQTT username (if your broker requires auth). |
| `mqtt_password`  | password (opt.) | `""`               | MQTT password (if your broker requires auth). |

**MQTT precedence (auto-first, fallback to explicit):** the broker is
**auto-discovered** from the Supervisor `mqtt` service — the Mosquitto add-on, or
any add-on that provides it — and injected into the add-on at start, so for most
installs you set **nothing**. The `mqtt_host`/`mqtt_port`/`mqtt_username`/
`mqtt_password` options above are a **fallback**, used only when the Supervisor
has no MQTT service to offer (e.g. an external broker like EMQX on another host).
When both are present, the Supervisor-provided broker wins.

Example configuration:

```yaml
timezone: America/New_York
pump_hours: 2.5
notify_targets:
  - mobile_app_your_phone
  - mobile_app_partner_phone
database_url: ""
```

## MQTT topics

The add-on connects to the broker discovered via the `mqtt` service and bridges
the following topics.

### Inbound (the add-on subscribes)

| Topic               | Payload                                          | Purpose                                                  |
| ------------------- | ------------------------------------------------ | -------------------------------------------------------- |
| `baby/remote/event` | `{"event_type": "...", "event_subtype": "..."}`  | Log an event from the ESP32 remote or an HA button.      |
| `baby/note`         | `{"message": "..."}`                             | Log a free-text note.                                    |

`event_type` values and their UI icons:
`feed` 🍼, `diaper` 🧷, `sleep` 😴, `bath` 🛁, `medicine` 💊,
`tummy_time` 🤸, `weight` ⚖️, `pump` 🤱, `note` 📝.

Common subtypes: feed → `breast`/`bottle`/`solid`; pump → `left`/`right`;
diaper → `pee`/`poop`/`both`/`change`.

Example (publish a bottle feed):

```bash
mosquitto_pub -t baby/remote/event \
  -m '{"event_type":"feed","event_subtype":"bottle"}'
```

### Outbound (the add-on publishes)

| Topic          | Retained | Purpose                                                                  |
| -------------- | -------- | ------------------------------------------------------------------------ |
| `baby/state`   | yes      | JSON stats snapshot (read by the auto-discovered sensors).               |
| `baby/status`  | yes      | Availability — `online` / `offline` (Last-Will).                         |

### Auto-created Home Assistant entities

On connect, the add-on publishes MQTT discovery so these appear under a single
**Baby Tracker** device with no manual YAML:

- Sensors: **Last Feed** (min), **Last Diaper** (min), **Feeds Today**,
  **Diapers Today**, **Sleep Today**.
- Binary sensor: **Sleeping** (occupancy).
- Buttons: Breast, Bottle, Solid, Pump L, Pump R, Pee, Poop, Both, Change,
  Sleep, Bath, Medicine, Tummy — each publishes the matching event to
  `baby/remote/event` when pressed.

## Phone notifications

To receive a push notification on each logged event (and pump reminders), add
one or more Home Assistant `notify` service names to `notify_targets`. Use the
service name **without** the `notify.` prefix — e.g. for
`notify.mobile_app_pixel_8` enter `mobile_app_pixel_8`.

These are typically the `mobile_app_*` services created by the Home Assistant
Companion app on each phone. Save the configuration and restart the add-on for
changes to take effect.

## Web UI (Ingress)

Click **Open Web UI** on the add-on page (or open the sidebar panel) to use the
tracker. It runs through Home Assistant Ingress under a path prefix, so the
front-end calls the API with relative URLs (`api/log`, `api/event`, …) and is
fully authenticated by Home Assistant — no extra port to expose and no separate
login.

## Data & persistence

Events are stored in SQLite at `/data/baby.db`, which persists across add-on
restarts and updates. The **Reset** action in the UI (`POST api/reset`) clears
all logged events — use with care.
