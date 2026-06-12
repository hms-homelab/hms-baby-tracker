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
| `pump_hours`     | float           | `2`                | Hours after a pump event before a pump reminder is fired (also the pump-due threshold shown on the remote's OLED). |
| `feed_hours`     | float           | `3`                | Hours after a feed event before a feed reminder is fired.                                   |
| `notify_targets` | list of strings | `[]`               | Home Assistant `notify` service names (without the `notify.` prefix) to send alerts to.     |
| `database_url`   | string (opt.)   | `""`               | Optional external database URL. Leave empty to use the built-in SQLite store under `/data`. |
| `mqtt_host`      | string (opt.)   | `""`               | MQTT broker host. **Leave blank to auto-discover the Mosquitto add-on**; set it (e.g. `192.168.1.15`) to point at an **external broker** like EMQX on another host. |
| `mqtt_port`      | port            | `1883`             | MQTT broker port. |
| `mqtt_username`  | string (opt.)   | `""`               | MQTT username (if your broker requires auth). |
| `mqtt_password`  | password (opt.) | `""`               | MQTT password (if your broker requires auth). |
| `ollama_enabled` | bool            | `false`            | Opt-in **Contraction AI assessment**. When on, each logged `contraction` event triggers an LLM labor-stage assessment of the last 2 hours of contractions. Off by default — only enable if you run a local [Ollama](https://ollama.com) server. |
| `ollama_url`     | string          | `http://192.168.2.5:11434` | Base URL of your Ollama server. |
| `ollama_model`   | string          | `gpt-oss:120b-cloud` | Ollama model used for the assessment (must be pulled on your server). |
| `ollama_timeout` | int             | `30`               | Seconds to wait for the Ollama response. |
| `ollama_prompt`  | string (opt.)   | `""`               | Optional prompt override. Leave blank for the built-in prompt. Supports `{count}`, `{avg_gap}`, `{avg_intensity}`, `{intensity_label}`, `{breakdown}`, `{shortest}`, `{longest}` placeholders. |

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
`tummy_time` 🤸, `weight` ⚖️, `pump` 🤱, `note` 📝, `contraction` ⏱️.

Common subtypes: feed → `breast`/`bottle`/`solid`; pump → `left`/`right`;
diaper → `pee`/`poop`/`both`/`change`.

Example (publish a bottle feed):

```bash
mosquitto_pub -t baby/remote/event \
  -m '{"event_type":"feed","event_subtype":"bottle"}'
```

### Outbound (the add-on publishes)

| Topic                  | Retained | Purpose                                                                  |
| ---------------------- | -------- | ------------------------------------------------------------------------ |
| `baby/state`           | yes      | JSON stats snapshot (read by the auto-discovered sensors).               |
| `baby/status`          | yes      | Availability — `online` / `offline` (Last-Will).                         |
| `baby/remote/display`  | yes      | `{"l1","l2","l3"}` — the 3 OLED rows for the Baby Remote (last feed/pump ago + pump ETA). Refreshed every 60 s and after each feed/pump. |
| `baby/remote/alert`    | yes      | `"1"`/`"0"` pump-due flag — the device pulses its LED and pops a banner on the rising edge. |
| `baby/remote/reminder` | no       | `{"l1","l2","secs"}` transient OLED banner — pushed when a feed reminder fires. |
| `baby/remote/history/replay` | no | `{"events":[…],"done":bool}` — chunked history backfill (see below).      |
| `baby/assessment`      | yes      | `{"text","time"}` — the Contraction AI assessment (only when `ollama_enabled`). |

### Auto-created Home Assistant entities

On connect, the add-on publishes MQTT discovery so these appear under a single
**Baby Tracker** device with no manual YAML:

- Sensors: **Last Feed** (min), **Last Diaper** (min), **Feeds Today**,
  **Diapers Today**, **Sleep Today**.
- Sensors (only when `ollama_enabled`): **Contraction Assessment** and
  **Contraction Assessment Time**.
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

### Build your own automation (MQTT)

If `notify_targets` doesn't fit your needs — e.g. you want to pick targets
dynamically, add conditions, or only notify on certain event types — the add-on
**publishes every stored event on MQTT** so you can trigger any automation:

- **Topic:** `baby/event` (non-retained — fires once per logged event)
- **Payload (JSON):** `event_type`, `event_subtype`, `note`, `logged_at`,
  `title`, `message`, `id`, `source` (`api` for the web UI/app REST, or `mqtt`
  for the remote/HA buttons).

This fires for **every** source and is independent of `notify_targets`. Example
— notify a phone on every feed:

```yaml
automation:
  - alias: Baby feed notification
    trigger:
      - platform: mqtt
        topic: baby/event
    condition:
      - "{{ trigger.payload_json.event_type == 'feed' }}"
    action:
      - service: notify.mobile_app_pixel_8
        data:
          title: "{{ trigger.payload_json.title }}"
          message: "{{ trigger.payload_json.message }}"
```

Drop the `condition` to notify on every event, or change it to match
`diaper`, `pump`, `sleep`, etc. Because this is a normal HA trigger, the
notify **target picker works** like any other automation.

> Note: `baby/event` is the **outbound** notification topic. The remote and HA
> buttons still publish presses on `baby/remote/event` (inbound) as before —
> they're kept separate to avoid a re-ingest loop.

## Web UI (Ingress)

Click **Open Web UI** on the add-on page (or open the sidebar panel) to use the
tracker. It runs through Home Assistant Ingress under a path prefix, so the
front-end calls the API with relative URLs (`api/log`, `api/event`, …) and is
fully authenticated by Home Assistant — no extra port to expose and no separate
login.

## Data & persistence

Events are stored in SQLite at `/data/baby.db` by default, which persists across
add-on restarts and updates. The **Reset** action in the UI (`POST api/reset`)
clears all logged events — use with care.

### External Postgres (optional, advanced)

Set `database_url` to point the add-on at an existing PostgreSQL database instead
of the built-in SQLite store — useful if you already keep a `baby_events` archive
elsewhere. The add-on reads and writes the standard `baby_events` table
(`id, event_type, event_subtype, note, logged_at`); it creates the table only if
it is absent and never drops existing data.

## Contraction AI assessment (optional, advanced)

If you run a local [Ollama](https://ollama.com) server, set `ollama_enabled: true`
to get a short, LLM-generated labor-stage assessment whenever a `contraction`
event is logged. On each contraction the add-on:

1. gathers all `contraction` events from the **last 2 hours**;
2. computes the contraction count, average/shortest/longest gap, and (when an
   intensity is recorded as the subtype or note: `mild`/`moderate`/`strong`/
   `intense`) the average intensity and breakdown;
3. asks Ollama (`POST {ollama_url}/api/generate`, `stream=false`) for a 2-sentence
   assessment naming the likely labor stage plus one practical suggestion;
4. publishes the result to the **Contraction Assessment** /
   **Contraction Assessment Time** sensors (via retained `baby/assessment`) and,
   best-effort, sets the legacy `input_text.ai_assessment` /
   `input_text.ai_assessment_time` entities so existing dashboards keep working.

With fewer than 2 contractions in the window no LLM call is made and the text is
set to `Need 2+ contractions in 2h`. The whole feature is gated behind
`ollama_enabled` and does nothing for installs without an LLM. This replaces the
former n8n "Contraction AI Assessment" workflow.

```yaml
database_url: "postgresql://USER:PASSWORD@HOST:5432/DBNAME"
```

Leave `database_url` empty to use SQLite (the default for most installs).

## Baby Remote history backfill (MQTT)

The Baby Remote app can backfill its local history from the server over MQTT.
Publish a request and the add-on replies with the full event stream:

- Request: `baby/remote/history/request` — `{"since": <unix_seconds>}` (0 = all).
- Reply: `baby/remote/history/replay` — `{"events": [{"id","ts","type","subtype","note"}], "done": <bool>}`.

`ts` is unix epoch seconds. Large result sets are split across multiple replay
messages; `done` is `true` only on the final (terminator) message.
