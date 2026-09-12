# SDD-001 — Baby Tracker App (Home Assistant Add-on / "App")

Status: **APPROVED** — decisions resolved 2026-06-02 (see §4)
Date: 2026-06-02
Component: `baby-tracker-app/` (new)

## 1. Goal

A **self-contained Home Assistant App** (technically an add-on; HA 2026.2
renamed add-ons → "Apps" in the UI only — the developer format is unchanged)
that replaces the n8n + external-PostgreSQL backend with **one Docker
container**. Anyone can install it from a repository URL in Settings → Apps,
with **no n8n and no external database** required.

It must reach **feature parity** with today's suite and be **portable to any
HA instance**.

## 2. Background — what it replaces

Current backend (to be retired for users who adopt the app):

| Piece | Today | Replaced by |
|---|---|---|
| Event ingest | n8n `POST /webhook/baby-event` | App REST `POST /api/event` + MQTT |
| Note ingest | n8n MQTT note logger | App MQTT subscriber |
| Stats/journal | n8n `GET /webhook/baby-log` (200 rows + computed stats) | App `GET /api/log` (same shape) |
| Pump reminder | n8n `POST /webhook/pump-reminder` → wait 2h → notify | App scheduler → HA notify |
| Storage | PostgreSQL `baby_events` on homelab.local | **SQLite in `/data`** (add-on volume) |
| Phone fan-out | n8n "Notify You/Partner" | HA `notify.*` via Supervisor proxy |
| Dashboard | hand-built Lovelace YAML | **Ingress web UI** (the colorful button grid) |
| Remote | ESP32-C3 → MQTT `baby/remote/event` | unchanged — app subscribes directly |

Data model (unchanged, ported to SQLite):
`baby_events(id, event_type, event_subtype, note, logged_at)`.

Event taxonomy (must preserve): feed{breast,bottle,solid}, pump{left,right},
diaper{pee,poop,both,change}, sleep(toggle), bath, medicine, tummy_time,
weight, note{regular,special}.

Stats contract (must reproduce exactly — consumed by the UI):
`last_feed_min/type`, `last_diaper_min/type`, `sleep_today` (minutes +
`is_sleeping`), counts today for feeds/diapers/pumps/baths/medicines/
tummy_times, plus the 200-row journal with `HH12:MI AM, Mon DD` formatted time.

## 3. Architecture

```
ESP32-C3 remote ─MQTT─┐
HA UI (Ingress card) ─┤
REST POST /api/event ─┼─▶ Baby Tracker App (Docker, /data SQLite)
                      │     ├─ ingest + store events
                      │     ├─ stats engine (parity w/ n8n Compute Stats)
                      │     ├─ scheduler (2h pump reminders)
                      │     ├─ Ingress web UI (button grid + journal + notes)
                      │     └─ MQTT discovery ─▶ HA entities + notify.*
                      ▼
              HA sensors (last feed, diapers today, sleeping…)
              + notifications to phones (notify via Supervisor proxy)
```

### 3.1 Add-on package layout
```
baby-tracker-app/
  repository.yaml          # makes the repo installable in HA
  baby-tracker/            # the app itself
    config.yaml            # name, ingress: true, hassio_api, homeassistant_api,
                           #   options/schema, mqtt service, map: [] (no host paths)
    build.yaml             # base images per arch (aarch64/amd64/armv7)
    Dockerfile
    run / app/             # the service
    rootfs/ or app/web/    # Ingress frontend assets
    icon.png logo.png CHANGELOG.md DOCS.md
```

### 3.2 Service responsibilities
- **HTTP (Ingress):** serve the SPA + JSON API (`/api/event`, `/api/log`,
  `/api/note`, `/api/reset`, `/api/pump-reminder`). Honor the
  `X-Ingress-Path` header so asset/API URLs work under the Ingress prefix.
- **MQTT:** subscribe `baby/remote/event` (ESP32) + publish **MQTT discovery**
  configs so `sensor.baby_last_feed`, `sensor.baby_diapers_today`,
  `binary_sensor.baby_sleeping`, and one `button.*` per event appear natively.
- **Scheduler:** per-side pump timers; on expiry call HA notify.
- **Notify:** `POST http://supervisor/core/api/services/notify/<target>` using
  `SUPERVISOR_TOKEN`; targets configurable in app options.

## 4. Decisions (resolved 2026-06-02)

1. **Stack** → **Python 3.12 + FastAPI + uvicorn + aiosqlite + aiomqtt +
   APScheduler**, frontend a Lit/vanilla SPA via Ingress. ✅
2. **Storage** → **SQLite in `/data`**, with optional `database_url` for an
   external Postgres. ✅
3. **Notifications** → HA `notify.*` via the Supervisor core proxy; targets in
   options, default off (silent until configured). ✅
4. **Remote↔app** → ESP32 firmware unchanged; app subscribes to
   `baby/remote/event`. ✅
5. **Distribution** → new top-level `baby-tracker-app/`; repo doubles as the
   installable HA app repository. ✅

## 5. Out of scope (v1)
- Multi-baby support, auth beyond HA Ingress, charts/graphs (journal list only),
  migrating existing Postgres rows (provide an optional import script later).

## 6. Acceptance / test plan
- **Parity:** golden-file test — feed the same event sequence to the app's stats
  engine and to a captured n8n `/baby-log` response; assert identical stats.
- **Ingress:** loads under HA Ingress, buttons log events, journal updates.
- **MQTT:** ESP32 (or `mosquitto_pub` to `baby/remote/event`) creates a row;
  discovery entities appear in HA.
- **Reminder:** pump press → notify fires ~2h later (test with short interval).
- **Persistence:** events survive app restart (SQLite in `/data`).
- **Install:** add repo URL → app installs/starts on amd64 + aarch64.

## 7. Versioning
New component `baby-tracker-app/VERSION` → **2026.1.0** (per the YYYY.ver.patch
scheme). Tagging only on user request.
```
