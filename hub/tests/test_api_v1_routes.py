"""
test_api_v1_routes.py — the spec surface is served alongside the legacy one.

Phase 1 of the framework convergence is purely additive: /api/v1/* appears, and
every pre-spec route keeps working byte for byte. These tests exist to catch the
regression that would actually hurt — someone "finishing" the migration by
deleting /edge/event while an edge device is still flashed to call it.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework import mqtt_bus  # noqa: E402
from framework.policy import Policy, Verdict  # noqa: E402
from framework.server import API_PREFIX, create_app  # noqa: E402


class _StubPolicy(Policy):
    name = "stub"

    def evaluate(self, image_path, event):
        return Verdict(verified=False, confidence=None, alert="stub")


class _StubBackend:
    def status(self):
        return {"available": False}


class _StubMQTT(_StubBackend):
    """Records every (topic, payload) instead of reaching a broker."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return True

    publish_command = mqtt_bus.MQTTBus.publish_command
    is_available = lambda self: True  # noqa: E731

    def topics(self):
        return [t for t, _ in self.published]


@pytest.fixture
def client(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(
        policy=_StubPolicy(), vlm=_StubBackend(), mqtt=_StubMQTT(),
        sms=_StubBackend(), static_dir=str(static),
    )
    app.config["TESTING"] = True
    return app.test_client()


# --- both names reach the same handler --------------------------------------

@pytest.mark.parametrize("path", ["/health", f"{API_PREFIX}/health"])
def test_health_served_under_both_names(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.get_json()["service"] == "qonclave-hub"


def test_health_bodies_are_identical(client):
    """Same handler, so the only field allowed to differ is the timestamp."""
    legacy = client.get("/health").get_json()
    spec = client.get(f"{API_PREFIX}/health").get_json()
    legacy.pop("time"), spec.pop("time")
    assert legacy == spec


@pytest.mark.parametrize("path", ["/edge/event", f"{API_PREFIX}/events"])
def test_event_ingest_served_under_both_names(client, path):
    resp = client.post(
        f"{path}?device_id=unoq-01&event_type=person_detected&edge_confidence=0.9",
        data=b"\xff\xd8\xff\xe0 not really a jpeg",
        content_type="image/jpeg",
    )
    assert resp.status_code == 200
    assert resp.get_json()["received"] is True


# --- unimplemented spec endpoints are 501, not 404 --------------------------

def test_unimplemented_endpoints_report_501_with_a_reason(client):
    for method, name in (("get", "capabilities"), ("post", "checkin"), ("post", "grants")):
        resp = getattr(client, method)(f"{API_PREFIX}/{name}")
        assert resp.status_code == 501, name
        body = resp.get_json()
        # 501 vs 404 is the point: a client probing for capabilities must be
        # able to tell "this hub doesn't do it" from "no such thing exists".
        assert body["error"] == "not_implemented"
        assert body["reason"]


# --- MQTT dual-publish ------------------------------------------------------

def test_command_goes_to_both_topic_layouts(monkeypatch):
    monkeypatch.setattr(mqtt_bus, "LEGACY_TOPICS", True)
    bus = _StubMQTT()
    assert bus.publish_command("unoq-01", {"type": "robot_move"}) is True
    assert bus.topics() == ["qonclave/commands/unoq-01", "qonclave/unoq-01/command"]


def test_legacy_topic_drops_out_when_disabled(monkeypatch):
    monkeypatch.setattr(mqtt_bus, "LEGACY_TOPICS", False)
    bus = _StubMQTT()
    assert bus.publish_command("unoq-01", {"type": "robot_move"}) is True
    assert bus.topics() == ["qonclave/commands/unoq-01"]


def test_partial_publish_failure_is_not_reported_as_success(monkeypatch):
    """A device that may not have been reached must not answer 200."""
    monkeypatch.setattr(mqtt_bus, "LEGACY_TOPICS", True)
    bus = _StubMQTT()
    bus.publish = lambda topic, payload: topic.startswith("qonclave/commands/")
    assert bus.publish_command("unoq-01", {"type": "robot_move"}) is False


def test_spec_and_legacy_topics_are_distinct_shapes():
    """Guards the migration invariant: an edge subscribing to one layout can
    never also match the other, so dual-publish cannot double-deliver."""
    assert mqtt_bus.command_topic("n1") == "qonclave/commands/n1"
    assert mqtt_bus.legacy_command_topic("n1") == "qonclave/n1/command"
    assert mqtt_bus.command_topic("n1") != mqtt_bus.legacy_command_topic("n1")
