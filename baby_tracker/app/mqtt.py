"""MQTT bridge: ingest from the ESP32 remote + expose native HA entities.

Inbound:
  baby/remote/event           {"event_type","event_subtype"}  (ESP32 buttons + HA buttons)
  baby/note                   {"message"}                      (note logger)
  baby/remote/history/request {"since": <unix_seconds_int>}    (app backfill request)

Outbound:
  homeassistant/.../config    MQTT discovery for sensors/binary_sensor/buttons
  baby/state                  retained JSON stats (sensors read via value_template)
  baby/status                 availability (online/offline LWT)
  baby/remote/history/replay  {"events":[...], "done":bool}  chunked history backfill
  baby/remote/display         retained {"l1","l2","l3"}  3-row OLED text (device)
  baby/remote/alert           retained "1"/"0"  pump-due flag (device LED + banner)
  baby/remote/reminder        {"l1","l2","secs"}  transient OLED banner (device)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import aiomqtt

log = logging.getLogger("baby.mqtt")

STATE_TOPIC = "baby/state"
STATUS_TOPIC = "baby/status"
EVENT_TOPIC = "baby/remote/event"
# Outbound: every stored event is re-fired here so HA automations (MQTT trigger
# on `baby/event`) can notify phones — for ANY source (web UI, app REST, or the
# remote). Distinct from the INbound EVENT_TOPIC to avoid a re-ingest loop.
LOGGED_EVENT_TOPIC = "baby/event"
NOTE_TOPIC = "baby/note"
HISTORY_REQUEST_TOPIC = "baby/remote/history/request"
HISTORY_REPLAY_TOPIC = "baby/remote/history/replay"
DISPLAY_TOPIC = "baby/remote/display"
ALERT_TOPIC = "baby/remote/alert"
REMINDER_TOPIC = "baby/remote/reminder"
ASSESSMENT_TOPIC = "baby/assessment"  # retained {"text","time"} contraction AI assessment
HISTORY_CHUNK = 200  # events per replay message
DISCOVERY_PREFIX = "homeassistant"

DEVICE = {
    "identifiers": ["baby_tracker"],
    "name": "Baby Tracker",
    "manufacturer": "Smart Home Maestro",
    "model": "Baby Tracker App",
}

# (object_id, friendly name, value_template, unit)
SENSORS = [
    ("last_feed", "Last Feed", "{{ value_json.last_feed_min }}", "min"),
    ("last_diaper", "Last Diaper", "{{ value_json.last_diaper_min }}", "min"),
    ("feeds_today", "Feeds Today", "{{ value_json.feeds_today }}", None),
    ("diapers_today", "Diapers Today", "{{ value_json.diapers_today }}", None),
    ("sleep_today", "Sleep Today", "{{ value_json.sleep_total_today }}", None),
]

# (object_id, friendly name, event_type, event_subtype)
BUTTONS = [
    ("breast", "Breast", "feed", "breast"),
    ("bottle", "Bottle", "feed", "bottle"),
    ("solid", "Solid", "feed", "solid"),
    ("pump_left", "Pump L", "pump", "left"),
    ("pump_right", "Pump R", "pump", "right"),
    ("pee", "Pee", "diaper", "pee"),
    ("poop", "Poop", "diaper", "poop"),
    ("both", "Both", "diaper", "both"),
    ("change", "Change", "diaper", "change"),
    ("sleep_start", "Sleep Start", "sleep", "start"),
    ("sleep_end", "Sleep End", "sleep", "end"),
    ("bath", "Bath", "bath", ""),
    ("medicine", "Medicine", "medicine", ""),
    ("tummy", "Tummy", "tummy_time", ""),
]


class MqttBridge:
    def __init__(self, cfg, db=None):
        self.cfg = cfg
        self.db = db  # used to serve baby/remote/history/request
        self._client: aiomqtt.Client | None = None
        self.on_event = None    # async (event_type, subtype, note, source) -> None
        self.on_connect = None  # async () -> None, called once per (re)connect

    @property
    def enabled(self) -> bool:
        # Run whenever a broker host is known — from the add-on Configuration
        # (external broker like EMQX) or auto-discovered Mosquitto. The host is
        # the single source of truth; don't gate on a separate enabled flag.
        return bool(self.cfg.mqtt_host)

    async def run(self) -> None:
        if not self.enabled:
            log.info("MQTT disabled (no host); skipping bridge")
            return
        while True:
            try:
                will = aiomqtt.Will(STATUS_TOPIC, "offline", qos=1, retain=True)
                async with aiomqtt.Client(
                    hostname=self.cfg.mqtt_host,
                    port=self.cfg.mqtt_port,
                    username=self.cfg.mqtt_username,
                    password=self.cfg.mqtt_password,
                    will=will,
                ) as client:
                    self._client = client
                    await client.publish(STATUS_TOPIC, "online", qos=1, retain=True)
                    await self._publish_discovery()
                    await client.subscribe(EVENT_TOPIC)
                    await client.subscribe(NOTE_TOPIC)
                    await client.subscribe(HISTORY_REQUEST_TOPIC)
                    log.info("MQTT connected to %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
                    if self.on_connect:
                        with contextlib.suppress(Exception):
                            await self.on_connect()
                    async for msg in client.messages:
                        await self._handle(str(msg.topic), msg.payload)
            except aiomqtt.MqttError as e:
                self._client = None
                log.warning("MQTT error: %s; reconnecting in 5s", e)
                await asyncio.sleep(5)

    async def _handle(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            data = {"message": payload.decode(errors="replace")}
        if topic == HISTORY_REQUEST_TOPIC:
            await self.handle_history_request(data)
            return
        if not self.on_event:
            return
        if topic == NOTE_TOPIC:
            await self.on_event("note", None, data.get("message"), "mqtt")
        else:  # EVENT_TOPIC
            et = data.get("event_type")
            if et:
                await self.on_event(et, data.get("event_subtype") or None,
                                    data.get("note"), "mqtt")

    async def handle_history_request(self, data: dict) -> None:
        """Reply to a Baby Remote backfill request on baby/remote/history/replay.

        Queries baby_events ASC (optionally filtered by `since` unix seconds),
        maps each row to {id, ts(epoch s), type, subtype, note}, and publishes in
        chunks of HISTORY_CHUNK. `done` is true only on the final message; an
        empty {"events":[],"done":true} terminator is always sent so the app
        knows the stream finished even when the result set is empty."""
        if self._client is None or self.db is None:
            return
        try:
            since = int(data.get("since") or 0)
        except (TypeError, ValueError):
            since = 0
        try:
            events = await self.db.history(since)
        except Exception as e:  # don't kill the message loop on a bad query
            log.warning("history request failed: %s", e)
            return

        payloads = [
            {
                "id": e["id"],
                "ts": e["ts"],
                "type": e["event_type"],
                "subtype": e["event_subtype"],
                "note": e["note"],
            }
            for e in events
        ]
        n = len(payloads)
        log.info("history replay: %d events since=%s", n, since)
        # Chunk; mark done only on the final terminator message.
        for i in range(0, n, HISTORY_CHUNK):
            chunk = payloads[i:i + HISTORY_CHUNK]
            await self._client.publish(
                HISTORY_REPLAY_TOPIC,
                json.dumps({"events": chunk, "done": False}),
                qos=1,
            )
        await self._client.publish(
            HISTORY_REPLAY_TOPIC,
            json.dumps({"events": [], "done": True}),
            qos=1,
        )

    async def publish_event(self, row: dict) -> None:
        """Fire a stored event on `baby/event` for HA MQTT-trigger automations.

        Non-retained: this is a fire-once signal, not state — retaining it would
        re-trigger every listening automation on each HA/broker restart. Best
        effort; no-op until the broker is connected.
        """
        if self._client is None:
            return
        try:
            await self._client.publish(LOGGED_EVENT_TOPIC, json.dumps(row), qos=0, retain=False)
        except aiomqtt.MqttError as e:
            log.warning("publish_event failed: %s", e)

    async def publish_state(self, stats: dict) -> None:
        if self._client is None:
            return
        try:
            await self._client.publish(STATE_TOPIC, json.dumps(stats), qos=0, retain=True)
        except aiomqtt.MqttError as e:
            log.warning("publish_state failed: %s", e)

    async def publish_display(self, payloads: dict) -> None:
        """Push the 3-row OLED text + pump-due flag (mirrors the n8n Display flow).

        `payloads` = {"l1","l2","l3","alert"}. Display and alert are RETAINED so
        the device renders correctly after a reconnect/boot. Matches the
        firmware's `baby/remote/display` (JSON l1/l2/l3) + `baby/remote/alert`
        ("1"/"0") subscriptions.
        """
        if self._client is None:
            return
        display = {"l1": payloads.get("l1", ""),
                   "l2": payloads.get("l2", ""),
                   "l3": payloads.get("l3", "")}
        alert = str(payloads.get("alert", "0"))
        try:
            await self._client.publish(DISPLAY_TOPIC, json.dumps(display), qos=0, retain=True)
            # The firmware chimes on EVERY received alert "1" (integrations.c — no
            # rising-edge tracking), so re-emitting a steady "1" on each 60s display
            # refresh beeps the piezo every minute. Publish the alert ONLY when it
            # changes → chime fires once, on the real 0→1 transition. Still retained
            # so a reconnecting device gets the current pump-due state.
            if alert != getattr(self, "_last_alert", None):
                await self._client.publish(ALERT_TOPIC, alert, qos=0, retain=True)
                self._last_alert = alert
        except aiomqtt.MqttError as e:
            log.warning("publish_display failed: %s", e)

    async def publish_assessment(self, text: str, time_str: str) -> None:
        """Publish the contraction AI assessment (n8n "Update HA Assessment").

        Retained {"text","time"} on `baby/assessment`; the two discovery sensors
        (sensor.baby_contraction_assessment[_time]) read it via value_template,
        so an HA dashboard gets the same value the n8n input_text held.
        """
        if self._client is None:
            return
        payload = {"text": (text or "")[:255], "time": time_str or ""}
        try:
            await self._client.publish(ASSESSMENT_TOPIC, json.dumps(payload),
                                       qos=0, retain=True)
        except aiomqtt.MqttError as e:
            log.warning("publish_assessment failed: %s", e)

    async def publish_reminder(self, l1: str, l2: str, secs: int = 4) -> None:
        """Pop a transient two-line banner on the device OLED.

        Non-retained (shows once) — matches the firmware `baby/remote/reminder`
        {"l1","l2","secs"} handler used by the n8n feed reminder.
        """
        if self._client is None:
            return
        payload = {"l1": l1, "l2": l2, "secs": secs}
        try:
            await self._client.publish(REMINDER_TOPIC, json.dumps(payload), qos=0, retain=False)
        except aiomqtt.MqttError as e:
            log.warning("publish_reminder failed: %s", e)

    async def _publish_discovery(self) -> None:
        c = self._client
        if c is None:
            return
        common = {"availability_topic": STATUS_TOPIC, "device": DEVICE}
        for oid, name, tmpl, unit in SENSORS:
            cfg = {
                "name": name,
                "unique_id": f"baby_{oid}",
                "state_topic": STATE_TOPIC,
                "value_template": tmpl,
                **common,
            }
            if unit:
                cfg["unit_of_measurement"] = unit
            await c.publish(f"{DISCOVERY_PREFIX}/sensor/baby_tracker/{oid}/config",
                            json.dumps(cfg), qos=1, retain=True)
        # binary_sensor: sleeping
        await c.publish(
            f"{DISCOVERY_PREFIX}/binary_sensor/baby_tracker/sleeping/config",
            json.dumps({
                "name": "Sleeping",
                "unique_id": "baby_sleeping",
                "state_topic": STATE_TOPIC,
                "value_template": "{{ 'ON' if value_json.is_sleeping else 'OFF' }}",
                "device_class": "occupancy",
                **common,
            }), qos=1, retain=True)
        # contraction AI assessment text sensors (only when the LLM is enabled)
        if getattr(self.cfg, "ollama_enabled", False):
            await c.publish(
                f"{DISCOVERY_PREFIX}/sensor/baby_tracker/contraction_assessment/config",
                json.dumps({
                    "name": "Contraction Assessment",
                    "unique_id": "baby_contraction_assessment",
                    "state_topic": ASSESSMENT_TOPIC,
                    "value_template": "{{ value_json.text }}",
                    "icon": "mdi:timer-sand",
                    **common,
                }), qos=1, retain=True)
            await c.publish(
                f"{DISCOVERY_PREFIX}/sensor/baby_tracker/contraction_assessment_time/config",
                json.dumps({
                    "name": "Contraction Assessment Time",
                    "unique_id": "baby_contraction_assessment_time",
                    "state_topic": ASSESSMENT_TOPIC,
                    "value_template": "{{ value_json.time }}",
                    "icon": "mdi:clock-outline",
                    **common,
                }), qos=1, retain=True)
        # buttons
        for oid, name, et, st in BUTTONS:
            press = {"event_type": et}
            if st:
                press["event_subtype"] = st
            await c.publish(
                f"{DISCOVERY_PREFIX}/button/baby_tracker/{oid}/config",
                json.dumps({
                    "name": name,
                    "unique_id": f"baby_btn_{oid}",
                    "command_topic": EVENT_TOPIC,
                    "payload_press": json.dumps(press),
                    **common,
                }), qos=1, retain=True)
