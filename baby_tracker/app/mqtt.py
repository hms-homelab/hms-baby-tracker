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
SUPPLY_REMINDER_TOPIC = "baby/supply/reminder"  # non-retained {"title","message","supply"} (legacy alias of baby/alert)
# Unified app-level notifications bus. ONE topic HA automations subscribe to for
# every actionable alert: {kind, title, message, ...}. Distinct from
# baby/remote/alert (the device pump-due LED flag). Non-retained (fire-once).
ALERT_TOPIC = "baby/alert"
SUMMARY_TOPIC = "baby/summary"  # retained {"text","time","source"} AI daily summary
HISTORY_CHUNK = 200  # events per replay message
DISCOVERY_PREFIX = "homeassistant"

DEVICE = {
    "identifiers": ["baby_tracker"],
    "name": "Baby Tracker",
    "manufacturer": "Smart Home Maestro",
    "model": "Baby Tracker App",
}

# (object_id, friendly name, value_template, unit, device_class)
# "Last Feed"/"Last Diaper" are timestamp sensors (the actual event time), NOT a
# minute count: a snapshot count published into retained state is frozen between
# publishes (reads 0 right after a log) and the entity's last-changed is polluted
# by unrelated edits/deletes (issue #6). As a timestamp device_class HA renders a
# live, self-ticking "X minutes ago" that tracks the real event and nothing else.
SENSORS = [
    ("last_feed", "Last Feed", "{% if value_json.last_feed_at %}{{ value_json.last_feed_at }}{% endif %}", None, "timestamp"),
    ("last_diaper", "Last Diaper", "{% if value_json.last_diaper_at %}{{ value_json.last_diaper_at }}{% endif %}", None, "timestamp"),
    ("feeds_today", "Feeds Today", "{{ value_json.feeds_today }}", None, None),
    ("diapers_today", "Diapers Today", "{{ value_json.diapers_today }}", None, None),
    ("sleep_today", "Sleep Today", "{{ value_json.sleep_total_today }}", None, None),
    ("contractions_today", "Contractions Today", "{{ value_json.contractions_today }}", None, None),
    ("get_ready", "Get Ready", "{{ value_json.checklist_done }}/{{ value_json.checklist_total }}", None, None),
    ("supplies_low", "Low Supplies", "{{ value_json.supplies_low }}", None, None),
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
        self.on_event = None    # async (event_type, subtype, note, source, logged_at=None, value=None, value_unit=None) -> None
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
                # Forward the optional logged_at (backfill time for events logged
                # while a client was offline) + numeric value/value_unit. All
                # additive + optional: a client that omits them still gets now()
                # and no value, so existing publishers (device, HA buttons) are
                # unchanged. create_event/ingest_and_broadcast already accept them.
                await self.on_event(et, data.get("event_subtype") or None,
                                    data.get("note"), "mqtt",
                                    data.get("logged_at"),
                                    data.get("value"), data.get("value_unit"))

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

    async def publish_summary(self, text: str, time_str: str, source: str) -> None:
        """Publish the latest AI daily summary on `baby/summary` (retained), read
        by the sensor.baby_summary discovery entity."""
        if self._client is None:
            return
        payload = {"text": (text or "")[:1000], "time": time_str or "", "source": source}
        try:
            await self._client.publish(SUMMARY_TOPIC, json.dumps(payload), qos=0, retain=True)
        except aiomqtt.MqttError as e:
            log.warning("publish_summary failed: %s", e)

    async def publish_alert(self, kind: str, title: str, message: str,
                            extra: dict | None = None) -> None:
        """Fire an actionable alert on the unified `baby/alert` bus.

        `kind` ∈ fever / supply_low / supply_due / feed_reminder / pump_reminder.
        One topic for HA automations to trigger on and branch by `kind`.
        Non-retained; best effort; no-op until the broker is connected.
        """
        if self._client is None:
            return
        payload = {"kind": kind, "title": title, "message": message}
        if extra:
            payload.update(extra)
        try:
            await self._client.publish(ALERT_TOPIC, json.dumps(payload), qos=0, retain=False)
        except aiomqtt.MqttError as e:
            log.warning("publish_alert failed: %s", e)

    async def publish_supply_reminder(self, title: str, message: str,
                                      supply: dict | None = None) -> None:
        """Fire a supply low/refill-due reminder on `baby/supply/reminder`.

        Non-retained fire-once signal for HA MQTT-trigger automations, mirroring
        `baby/event`. Best effort; no-op until the broker is connected.
        """
        if self._client is None:
            return
        payload = {"title": title, "message": message}
        if supply is not None:
            payload["supply"] = {k: supply.get(k) for k in ("id", "category", "name")}
        try:
            await self._client.publish(SUPPLY_REMINDER_TOPIC, json.dumps(payload),
                                       qos=0, retain=False)
        except aiomqtt.MqttError as e:
            log.warning("publish_supply_reminder failed: %s", e)

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
        for oid, name, tmpl, unit, device_class in SENSORS:
            cfg = {
                "name": name,
                "unique_id": f"baby_{oid}",
                "state_topic": STATE_TOPIC,
                "value_template": tmpl,
                **common,
            }
            if unit:
                cfg["unit_of_measurement"] = unit
            if device_class:
                cfg["device_class"] = device_class
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
        # AI daily summary text sensor (only when the feature is enabled)
        if getattr(self.cfg, "summary_enabled", False):
            await c.publish(
                f"{DISCOVERY_PREFIX}/sensor/baby_tracker/summary/config",
                json.dumps({
                    "name": "Daily Summary",
                    "unique_id": "baby_summary",
                    "state_topic": SUMMARY_TOPIC,
                    "value_template": "{{ value_json.text }}",
                    "icon": "mdi:robot-happy-outline",
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
