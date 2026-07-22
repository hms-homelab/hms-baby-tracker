"""Tests for firing stored events on MQTT (app.mqtt.MqttBridge.publish_event).

Every stored event is re-published on `baby/event` (non-retained) so HA
automations can trigger on it and notify phones. We verify the topic/payload
and the no-op-without-a-connected-client path.
"""
import asyncio
import json

from app.config import Config
from app.mqtt import EVENT_TOPIC, LOGGED_EVENT_TOPIC, MqttBridge


class _FakeClient:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def test_publish_event_fires_on_baby_event_topic():
    bridge = MqttBridge(Config())
    fake = _FakeClient()
    bridge._client = fake
    row = {
        "event_type": "feed",
        "event_subtype": "breast",
        "title": "🍼 Feed (breast)",
        "message": "🍼 Feed (breast) at 2:00 PM",
        "source": "api",
    }
    asyncio.run(bridge.publish_event(row))

    assert len(fake.published) == 1
    topic, payload, qos, retain = fake.published[0]
    assert topic == LOGGED_EVENT_TOPIC == "baby/event"
    assert retain is False  # fire-once signal, not state
    assert json.loads(payload) == row


def test_publish_event_noop_without_client():
    bridge = MqttBridge(Config())
    bridge._client = None  # broker not connected yet
    # Must not raise.
    asyncio.run(bridge.publish_event({"event_type": "feed"}))


# --- inbound baby/remote/event: optional logged_at / value forwarding ----------
# The MQTT handler forwards optional logged_at + value/value_unit so a client can
# backfill an offline event's real time and carry a numeric reading. This is
# additive: a payload without those fields must behave exactly as before (now(),
# no value) — so existing publishers (device, HA buttons) are unaffected.

def _handle_event(payload: dict):
    bridge = MqttBridge(Config())
    captured = []

    async def fake_on_event(*args):
        captured.append(args)

    bridge.on_event = fake_on_event
    asyncio.run(bridge._handle(EVENT_TOPIC, json.dumps(payload).encode()))
    return captured


def test_handle_event_forwards_logged_at_and_value():
    captured = _handle_event({
        "event_type": "temperature", "event_subtype": "", "note": "warm",
        "logged_at": "2026-07-22T03:00:00+00:00", "value": 38.2, "value_unit": "C",
    })
    assert len(captured) == 1
    et, subtype, note, source, logged_at, value, value_unit = captured[0]
    assert et == "temperature"
    assert source == "mqtt"
    assert logged_at == "2026-07-22T03:00:00+00:00"
    assert value == 38.2
    assert value_unit == "C"


def test_handle_event_backward_compat_no_extra_fields():
    # A legacy payload (type+subtype only) must pass None for the new fields —
    # identical to the pre-change default, so now() + no value is preserved.
    captured = _handle_event({"event_type": "feed", "event_subtype": "breast"})
    assert len(captured) == 1
    et, subtype, note, source, logged_at, value, value_unit = captured[0]
    assert (et, subtype, source) == ("feed", "breast", "mqtt")
    assert logged_at is None
    assert value is None
    assert value_unit is None
