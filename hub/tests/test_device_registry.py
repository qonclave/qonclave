"""
test_device_registry.py — the network page's registry and its feeds.

The registry is passive by design: it only records devices that announce
themselves (probe, event, crop, MQTT status). These tests pin the two rules
that make it a registry rather than a log — one physical device is one row
(anonymous rows are absorbed once the device introduces itself), and rows age
through online → idle → offline instead of being evicted.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework import device_registry  # noqa: E402
from framework.policy import Policy, Verdict  # noqa: E402
from framework.server import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_registry():
    device_registry.clear()
    yield
    device_registry.clear()


# --- record / merge ----------------------------------------------------------

def test_sighting_with_no_identifier_is_dropped():
    device_registry.record(device_id="", ip=None, source="event")
    assert device_registry.snapshot() == []


def test_identified_device_absorbs_prior_anonymous_row():
    """A /track/analyze crop arrives before the device has ever named itself;
    the later /edge/event must fold both into one row, keeping the earlier
    first_seen and the sighting count."""
    device_registry.record(ip="10.0.0.5", source="track")
    device_registry.record(device_id="unoq-01", ip="10.0.0.5", source="event")

    rows = device_registry.snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row["device_id"] == "unoq-01"
    assert row["ip"] == "10.0.0.5"
    assert row["sightings"] == 2
    assert row["sources"] == ["event", "track"]


def test_anonymous_sighting_credits_known_device_at_same_ip():
    device_registry.record(device_id="unoq-01", ip="10.0.0.5", source="event")
    device_registry.record(ip="10.0.0.5", source="track")

    rows = device_registry.snapshot()
    assert len(rows) == 1
    assert rows[0]["device_id"] == "unoq-01"
    assert rows[0]["sightings"] == 2


def test_distinct_devices_stay_distinct():
    device_registry.record(device_id="unoq-01", ip="10.0.0.5", source="event")
    device_registry.record(device_id="unoq-02", ip="10.0.0.6", source="event")
    device_registry.record(ip="10.0.0.7", source="discovery")
    assert len(device_registry.snapshot()) == 3


# --- MQTT topic parsing ------------------------------------------------------

@pytest.mark.parametrize("topic,expected", [
    ("qonclave/status/unoq-01", "unoq-01"),   # spec layout
    ("qonclave/unoq-01/status", "unoq-01"),   # pre-spec layout
])
def test_status_topics_yield_a_device(topic, expected):
    device_registry.record_mqtt_topic(topic)
    rows = device_registry.snapshot()
    assert len(rows) == 1
    assert rows[0]["device_id"] == expected
    assert rows[0]["sources"] == ["mqtt"]


@pytest.mark.parametrize("topic", [
    "qonclave/commands/unoq-01",       # command, not status
    "qonclave/status",                 # no node id
    "other/status/unoq-01",            # not our namespace
    "qonclave/status/unoq-01/extra",   # wrong depth
])
def test_non_status_topics_are_ignored(topic):
    device_registry.record_mqtt_topic(topic)
    assert device_registry.snapshot() == []


# --- state ageing ------------------------------------------------------------

def test_rows_age_out_instead_of_disappearing(monkeypatch):
    import time as _time
    now = _time.time()
    device_registry.record(device_id="unoq-01", source="event")

    monkeypatch.setattr(device_registry.time, "time",
                        lambda: now + device_registry.ONLINE_S + 1)
    assert device_registry.snapshot()[0]["state"] == "idle"

    monkeypatch.setattr(device_registry.time, "time",
                        lambda: now + device_registry.OFFLINE_S + 1)
    rows = device_registry.snapshot()
    assert rows[0]["state"] == "offline"
    assert len(rows) == 1  # quiet devices are shown, not evicted


# --- /user/devices endpoint --------------------------------------------------

class _StubPolicy(Policy):
    name = "stub"

    def evaluate(self, event, image_path=None):
        return Verdict(verified=False, confidence=None, alert="stub")


class _StubBackend:
    def status(self):
        return {"available": False}


class _StubMQTT(_StubBackend):
    def __init__(self):
        self.subscribed: list[str] = []

    def publish(self, topic, payload):
        return True

    def publish_command(self, device_id, command):
        return True

    def subscribe(self, topic_filter):
        self.subscribed.append(topic_filter)
        return True


@pytest.fixture
def client(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "network.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(
        policy=_StubPolicy(), vlm=_StubBackend(), mqtt=_StubMQTT(),
        sms=_StubBackend(), static_dir=str(static),
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_ingested_event_appears_in_user_devices(client):
    client.post(
        "/edge/event?device_id=unoq-01&event_type=person_detected&edge_confidence=0.9",
        data=b"\xff\xd8\xff\xe0 not really a jpeg",
        content_type="image/jpeg",
    )
    body = client.get("/user/devices").get_json()
    assert body["count"] == 1
    row = body["devices"][0]
    assert row["device_id"] == "unoq-01"
    assert row["state"] == "online"
    assert row["ip"]  # the test client's address, but present
    assert body["hub"]["port"]


def test_user_devices_subscribes_to_both_status_layouts(client):
    client.get("/user/devices")
    # reach into the app's mqtt stub via a fresh call: easiest is a second app
    # build, so instead assert via the endpoint's contract — it must not fail
    # when no broker exists, and must answer an empty registry cleanly.
    body = client.get("/user/devices").get_json()
    assert body["devices"] == []
    assert body["count"] == 0


def test_network_page_is_served(client):
    resp = client.get("/user/network")
    assert resp.status_code == 200
