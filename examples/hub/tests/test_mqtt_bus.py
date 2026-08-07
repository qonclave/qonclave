"""
test_mqtt_bus.py — MQTTBus against a real broker.

A real amqtt broker in-process (no external service needed) and the real MQTTBus
(paho underneath) talking to it. MQTTBus's paho client used to live behind a
qonclave.transport.mqtt.MQTTTransport wrapper in the SDK; that violated
CONVENTIONS.md's "transport/ holds the ABCs only, the client library is the
developer's choice" rule and was reverted the same day -- this file replaces
framework/sdk/python/tests/test_transport_mqtt.py, which tested the now-removed
SDK class.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework import device_registry  # noqa: E402
from framework.mqtt_bus import MQTTBus  # noqa: E402

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 18831


def _run_broker(loop, ready):
    from amqtt.broker import Broker
    asyncio.set_event_loop(loop)
    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"{BROKER_HOST}:{BROKER_PORT}"}},
        "sys_interval": 0,
        "auth": {"allow-anonymous": True},
    }

    async def _start():
        broker = Broker(config)
        await broker.start()
        return broker

    loop.run_until_complete(_start())
    ready.set()
    loop.run_forever()


@pytest.fixture(scope="module", autouse=True)
def broker():
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    thread = threading.Thread(target=_run_broker, args=(loop, ready), daemon=True)
    thread.start()
    assert ready.wait(timeout=10), "amqtt broker did not start in time"
    time.sleep(0.3)  # let the listener actually accept, past 'ready'
    yield
    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture(autouse=True)
def fresh_registry():
    device_registry.clear()
    yield
    device_registry.clear()


def _wait_for(predicate, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _bus(**kw) -> MQTTBus:
    return MQTTBus(host=BROKER_HOST, port=BROKER_PORT,
                   client_id=f"t-{time.monotonic_ns()}", **kw)


# --- connect / status ---------------------------------------------------------

def test_status_before_any_connect_attempt():
    bus = _bus()
    status = bus.status()
    assert status == {
        "available": False, "enabled": True, "host": BROKER_HOST, "port": BROKER_PORT,
        "connect_attempted": False, "connect_error": None,
    }


def test_is_available_connects_lazily_and_reports_true():
    bus = _bus()
    assert bus.is_available() is True
    assert bus.status()["available"] is True
    bus.close()


def test_disabled_bus_never_connects():
    bus = _bus(enabled=False)
    assert bus.is_available() is False
    assert bus.status()["connect_attempted"] is False


def test_unreachable_broker_publish_fails_without_raising():
    bus = MQTTBus(host=BROKER_HOST, port=1, client_id="unreachable")  # port 1: nothing listens
    assert bus.publish("some/topic", {"x": 1}) is False
    assert bus.status()["connect_error"] is not None


# --- publish / subscribe -------------------------------------------------------

def test_publish_and_subscribe_round_trip():
    sub = _bus()
    assert sub.subscribe("qonclave/test/roundtrip") is True
    time.sleep(0.3)  # let the SUBSCRIBE ack land before publishing

    pub = _bus()
    assert pub.publish("qonclave/test/roundtrip", {"hello": "world"}) is True

    assert _wait_for(lambda: len(sub.recent_messages()) == 1)
    msg = sub.recent_messages()[0]
    assert msg["topic"] == "qonclave/test/roundtrip"
    assert msg["payload"] == '{"hello": "world"}'

    sub.close()
    pub.close()


def test_subscribe_is_idempotent():
    sub = _bus()
    assert sub.subscribe("qonclave/test/idempotent") is True
    assert sub.subscribe("qonclave/test/idempotent") is True  # no-op, still True
    time.sleep(0.3)

    pub = _bus()
    pub.publish("qonclave/test/idempotent", {"n": 1})

    assert _wait_for(lambda: len(sub.recent_messages()) == 1)
    time.sleep(0.2)
    assert len(sub.recent_messages()) == 1  # not delivered twice

    sub.close()
    pub.close()


def test_wildcard_subscription_receives_matching_topics():
    sub = _bus()
    sub.subscribe("qonclave/status/+")
    time.sleep(0.3)

    pub = _bus()
    pub.publish("qonclave/status/unoq-01", {})
    pub.publish("qonclave/commands/unoq-01", {})  # must NOT match

    assert _wait_for(lambda: len(sub.recent_messages()) == 1)
    time.sleep(0.2)
    assert [m["topic"] for m in sub.recent_messages()] == ["qonclave/status/unoq-01"]

    sub.close()
    pub.close()


def test_status_topic_message_is_recorded_in_device_registry():
    sub = _bus()
    sub.subscribe("qonclave/status/+")
    time.sleep(0.3)

    pub = _bus()
    pub.publish("qonclave/status/unoq-01", {"online": True})

    assert _wait_for(lambda: len(device_registry.snapshot()) == 1)
    row = device_registry.snapshot()[0]
    assert row["device_id"] == "unoq-01"
    assert row["last_source"] == "mqtt"

    sub.close()
    pub.close()


# --- publish_command dual-topic ------------------------------------------------

def test_publish_command_dual_publishes_by_default(monkeypatch):
    import framework.mqtt_bus as mqtt_bus
    monkeypatch.setattr(mqtt_bus, "LEGACY_TOPICS", True)

    sub = _bus()
    sub.subscribe("qonclave/commands/unoq-01")
    sub.subscribe("qonclave/unoq-01/command")
    time.sleep(0.3)

    pub = _bus()
    assert pub.publish_command("unoq-01", {"type": "robot_move"}) is True

    assert _wait_for(lambda: len(sub.recent_messages()) == 2)
    topics = {m["topic"] for m in sub.recent_messages()}
    assert topics == {"qonclave/commands/unoq-01", "qonclave/unoq-01/command"}

    sub.close()
    pub.close()


def test_publish_command_single_topic_when_legacy_disabled(monkeypatch):
    import framework.mqtt_bus as mqtt_bus
    monkeypatch.setattr(mqtt_bus, "LEGACY_TOPICS", False)

    sub = _bus()
    sub.subscribe("qonclave/commands/unoq-02")
    time.sleep(0.3)

    pub = _bus()
    assert pub.publish_command("unoq-02", {"type": "robot_move"}) is True

    assert _wait_for(lambda: len(sub.recent_messages()) == 1)
    time.sleep(0.2)
    assert [m["topic"] for m in sub.recent_messages()] == ["qonclave/commands/unoq-02"]

    sub.close()
    pub.close()
