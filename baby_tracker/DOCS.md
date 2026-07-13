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
| `default_tab`    | list            | `baby`             | Which tab the web UI opens on (`get_ready`, `baby`, `contractions`, `health`, `growth`, `supplies`). Lead with `contractions`/`get_ready` pre-birth, then switch to `baby`. |
| `supply_reminder_hour` | int (0-23) | `9`               | Local hour for the daily supplies sweep that reminds you about low-stock and refill-due items. |
| `checklist_reset_hour` | int (0-23) | `0`               | Local hour to auto-uncheck the Get Ready checklist each morning. `0` = off (the default, since the seeded items are mostly one-time prep). |
| `fever_threshold_c` | float        | `38.0`             | A logged temperature at/above this (in °C) is flagged as a fever in the Health tab. °F entries are converted before comparing. |
| `measurement_system` | list        | `imperial`         | Default unit pickers in the UI: `imperial` (°F, lb/oz, in) or `metric` (°C, kg, cm). The unit is stored per entry, so this only changes the defaults. |
| `summary_enabled` | bool          | `false`            | The AI daily summary in the summary card. **Off by default (opt-in)** — turn it on to get a warm plain-language recap. When enabled it sends only a de-identified digest (counts/trends, never names or notes) to the chosen provider (hosted proxy by default). |
| `summary_provider` | list         | `hosted`           | LLM backend: `hosted` proxy (default, `babytracker.shmaestro.com`), your own `ollama`, or `anthropic` / `openai` / `gemini`. |
| `summary_hour` | int (0-23)       | `6`                | Local hour for the automatic daily digest. `0` = on-demand only. |
| `summary_daily_cap` | int         | `2`                | Max summaries per day (auto + on-demand combined). |
| `summary_hosted_url` | string      | `""`               | Base URL of the hosted summary proxy (when provider is `hosted`). |
| `summary_ollama_url` | string      | `http://192.168.2.5:11434` | Your Ollama server (when provider is `ollama`). |
| `summary_model` | string          | `gpt-oss:120b-cloud` | Model name for the chosen provider. |
| `summary_api_key` | password      | `""`               | API key for `anthropic` / `openai` / `gemini`. |
| `summary_prompt` | string         | (built-in)         | The recap instruction — pre-filled and editable. The de-identified digest is always appended by the add-on, so edits change tone, not what data is sent. |
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
| `baby/alert`           | no       | **Unified notifications bus** — `{"kind","title","message",…}` for every actionable alert. `kind` ∈ `fever`, `supply_low`, `supply_due`, `feed_reminder`, `pump_reminder`. Subscribe once and branch on `kind`. |
| `baby/supply/reminder` | no       | `{"title","message","supply"}` — legacy alias of the supply alerts on `baby/alert` (kept for 2026.4.0 automations). |
| `baby/summary`         | yes      | `{"text","time","source"}` — the latest AI daily summary (only when `summary_enabled`). |

### Auto-created Home Assistant entities

On connect, the add-on publishes MQTT discovery so these appear under a single
**Baby Tracker** device with no manual YAML:

- Sensors: **Last Feed** (timestamp), **Last Diaper** (timestamp), **Feeds
  Today**, **Diapers Today**, **Sleep Today**, **Contractions Today**, **Get
  Ready** (done/total), **Low Supplies** (count). Last Feed/Last Diaper are
  `timestamp` entities holding the actual event time, so a dashboard shows a
  live, self-ticking "x minutes ago" that stays accurate (it is not affected by
  unrelated logs, edits or deletes).
- Sensors (only when `ollama_enabled`): **Contraction Assessment** and
  **Contraction Assessment Time**.
- Binary sensor: **Sleeping** (occupancy).
- Buttons: Breast, Bottle, Solid, Pump L, Pump R, Pee, Poop, Both, Change,
  Sleep, Bath, Medicine, Tummy — each publishes the matching event to
  `baby/remote/event` when pressed.

## Phone notifications

Notifications are **MQTT-based only**: build a Home Assistant automation
against the events the add-on publishes (below). There is no built-in
`notify_targets` option; an earlier version had one, calling HA's `notify.*`
services through the Supervisor's API proxy, but that proxy call depends on a
per-add-on Supervisor token that has proven unreliable across real installs
(a known, long-standing, unresolved upstream Home Assistant Supervisor issue,
see the project changelog). MQTT has no such dependency, so it's the only
notification path now.

The add-on **publishes every stored event on MQTT** so you can trigger any
automation:

- **Topic:** `baby/event` (non-retained — fires once per logged event)
- **Payload (JSON):** `event_type`, `event_subtype`, `note`, `logged_at`,
  `title`, `message`, `id`, `source` (`api` for the web UI/app REST, or `mqtt`
  for the remote/HA buttons).

This fires for **every** source. Example — notify a phone on every feed:

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

### Tabs

A pinned **summary** sits on top, a shared **note bar** (with a ⭐ special
toggle) and a pinned **journal** (logging every tab's events) sit at the bottom;
between them is a tab bar:

- **Get Ready** — an editable prep checklist for mom, seeded with popular
  suggestions (crib, diaper bag, newborn clothes, bottles, wipes + cream, car
  seat). Tap to check off, add your own items, or **Uncheck all**. Optional daily
  auto-reset via `checklist_reset_hour`.
- **Baby** — the everyday logging surface (feed / pump / diaper / other, and
  add/backfill).
- **Contractions** — three big severity buttons (**Mild** / **Medium** /
  **Intense**), a note, and a contraction backfill, with a live "how many in the
  last 2h / last one / average gap" readout. Feeds the optional AI assessment.
- **Health** — log a temperature (°C or °F, flagged as a fever at/above
  `fever_threshold_c`), free-text symptom notes, and medicine doses (with a
  "last dose / N today" readout).
- **Growth** — log weight (lb + oz or kg), length, and head circumference; each
  shows the latest value, the change since the previous reading, and a small
  trend sparkline. Units default from `measurement_system`.
- **Supplies** — see below.

Which tab opens first is the `default_tab` option — set it to `contractions` or
`get_ready` for the pre-birth phase, then switch to `baby` after the arrival.

### Supplies

Track consumables (formula, diapers, wipes, creams, anything) so you know when to
restock. Each supply has a quantity + brand + type and two independent nudges:

- **Auto-count-down** — tick "Auto-count-down when I log its event" and pick the
  event (e.g. a **bottle feed** decrements formula, a **diaper** change decrements
  wipes). Every matching *live* event subtracts the configured amount; backfilled
  past events don't.
- **Refill reminders** — set a **low-stock threshold** (`remind at ≤`) and/or a
  **refill every N days** cadence. A daily sweep (at `supply_reminder_hour`) sends
  a notification and fires `baby/supply/reminder` for whichever applies; a low
  reminder also fires the moment stock crosses the threshold.

Use **−/＋** for quick corrections and **Refill** to restock (which resets the
cadence and logs a 🧴 refill row in the journal).

### Editing and backfilling events

Real life is messy — sometimes you log a feed late, or forget to log one until
the next change. Two affordances keep the timeline honest:

- **Add / backfill an event** card: pick a type, set a date/time (defaults to
  now), optionally add a note, and **Add**. Leave the time at "now" for a normal
  log, or set it in the past to fill in a missed event.
- **Tap any journal row** to open an inline editor: fix its time with the
  date/time picker, add or edit its note (pre-filled with the current note), then
  **Save**, or **Delete** the event entirely.

Under the hood these map to `POST api/event` (with an optional `logged_at`
ISO8601 timestamp), `PATCH api/event/{id}` (edit `logged_at` / `note` /
`event_subtype`), and `DELETE api/event/{id}`. Edits and deletes immediately
recompute the stats and refresh the device OLED, but — unlike a brand-new event
— they don't re-fire `baby/event` or send a push notification.

## Data & persistence

Events are stored in SQLite at `/data/baby.db` by default, which persists across
add-on restarts and updates. The **Reset** action in the UI (`POST api/reset`)
clears all logged events — use with care.

### Back up & restore

Use **Back up data** in the footer to download a single JSON file containing all
your events, supplies and checklist items (`GET api/export`). Keep it somewhere
safe; **Restore** (`POST api/import`) reloads it later and **replaces** the
current data. This is a per-add-on backup independent of Home Assistant's
system-wide snapshots, so you never lose your log to a bad update.

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
