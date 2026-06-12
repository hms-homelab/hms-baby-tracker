"""Tests for firing stored events on MQTT (app.mqtt.MqttBridge.publish_event).

Every stored event is re-published on `baby/event` (non-retained) so HA
automations can trigger on it and notify phones. We verify the topic/payload
and the no-op-without-a-connected-client path.
"""
import asyncio
import json

from app.config import Config
from app.mqtt import LOGGED_EVENT_TOPIC, MqttBridge


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
