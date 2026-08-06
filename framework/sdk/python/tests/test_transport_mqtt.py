"""
test_transport_mqtt.py — the generic MQTT pub/sub transport.

A real amqtt broker in-process (no external service needed) and a real MQTTTransport
(paho-mqtt) talking to it -- the same style already used to verify the edge's MQTT
client earlier the same day this migration landed.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from qonclave.transport.mqtt import MQTTTransport

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 18830


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


def _wait_for(predicate, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _transport(**kw):
    return MQTTTransport(host=BROKER_HOST, port=BROKER_PORT, client_id=f"t-{time.monotonic_ns()}",
                         **kw)


# --- connect / status ---------------------------------------------------------

def test_status_before_any_connect_attempt():
    t = _transport()
    status = t.status()
    assert status == {
        "available": False, "enabled": True, "host": BROKER_HOST, "port": BROKER_PORT,
        "connect_attempted": False, "connect_error": None,
    }


def test_is_available_connects_lazily_and_reports_true():
    t = _transport()
    assert t.is_available() is True
    assert t.status()["available"] is True
    t.close()


def test_disabled_transport_never_connects():
    t = _transport(enabled=False)
    assert t.is_available() is False
    assert t.status()["connect_attempted"] is False


def test_unreachable_broker_publish_fails_without_raising():
    t = MQTTTransport(host=BROKER_HOST, port=1, client_id="unreachable")  # port 1: nothing listens
    assert t.publish("some/topic", b"x") is False
    assert t.status()["connect_error"] is not None


# --- publish / subscribe -------------------------------------------------------

def test_publish_and_subscribe_round_trip():
    received = []
    sub = _transport()
    sub.subscribe("qonclave/test/roundtrip", lambda topic, body: received.append((topic, body)))
    time.sleep(0.3)  # let the SUBSCRIBE ack land before publishing

    pub = _transport()
    assert pub.publish("qonclave/test/roundtrip", b"hello") is True

    assert _wait_for(lambda: len(received) == 1)
    assert received[0] == ("qonclave/test/roundtrip", b"hello")

    sub.close()
    pub.close()


def test_subscribe_is_idempotent_by_topic():
    calls_a, calls_b = [], []
    t = _transport()
    t.subscribe("qonclave/test/idempotent", lambda topic, body: calls_a.append(body))
    t.subscribe("qonclave/test/idempotent", lambda topic, body: calls_b.append(body))  # ignored
    time.sleep(0.3)

    pub = _transport()
    pub.publish("qonclave/test/idempotent", b"once")

    assert _wait_for(lambda: len(calls_a) == 1)
    time.sleep(0.2)
    assert calls_b == []  # second subscribe() never registered

    t.close()
    pub.close()


def test_wildcard_subscription_receives_matching_topics():
    received = []
    sub = _transport()
    sub.subscribe("qonclave/status/+", lambda topic, body: received.append(topic))
    time.sleep(0.3)

    pub = _transport()
    pub.publish("qonclave/status/unoq-01", b"{}")
    pub.publish("qonclave/commands/unoq-01", b"{}")  # must NOT match

    assert _wait_for(lambda: len(received) == 1)
    time.sleep(0.2)
    assert received == ["qonclave/status/unoq-01"]

    sub.close()
    pub.close()
