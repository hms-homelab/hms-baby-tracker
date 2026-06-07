"""MQTT bridge: ingest from the ESP32 remote + expose native HA entities.

Inbound:
  baby/remote/event  {"event_type","event_subtype"}  (ESP32 buttons + HA buttons)
  baby/note          {"message"}                       (note logger)

Outbound:
  homeassistant/.../config  MQTT discovery for sensors/binary_sensor/buttons
  baby/state                retained JSON stats (sensors read via value_template)
  baby/status               availability (online/offline LWT)
"""
from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

log = logging.getLogger("baby.mqtt")

STATE_TOPIC = "baby/state"
STATUS_TOPIC = "baby/status"
EVENT_TOPIC = "baby/remote/event"
NOTE_TOPIC = "baby/note"
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
    def __init__(self, cfg):
        self.cfg = cfg
        self._client: aiomqtt.Client | None = None
        self.on_event = None  # async (event_type, subtype, note, source) -> None

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
                    log.info("MQTT connected to %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
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
        if not self.on_event:
            return
        if topic == NOTE_TOPIC:
            await self.on_event("note", None, data.get("message"), "mqtt")
        else:  # EVENT_TOPIC
            et = data.get("event_type")
            if et:
                await self.on_event(et, data.get("event_subtype") or None,
                                    data.get("note"), "mqtt")

    async def publish_state(self, stats: dict) -> None:
        if self._client is None:
            return
        try:
            await self._client.publish(STATE_TOPIC, json.dumps(stats), qos=0, retain=True)
        except aiomqtt.MqttError as e:
            log.warning("publish_state failed: %s", e)

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
