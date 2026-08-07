"""
test_sms_routes.py — apps/security/sms_routes.py's blueprint.

POST /sms and GET /user/sms_activity moved out of framework/server.py in the
sms_bus.py migration (2026-08-06) since both read Twilio's own wire shapes.
Nothing exercised them directly before that move either, so this is new
coverage, not a port.
"""

from __future__ import annotations

import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qonclave.core.models import Command  # noqa: E402
from framework.events import default_store  # noqa: E402
from framework.policy import Policy, Verdict  # noqa: E402
from apps.security.sms_routes import create_sms_blueprint  # noqa: E402


class _StubPolicy(Policy):
    name = "stub"

    def __init__(self):
        self.replies: list[tuple[str, str]] = []

    def evaluate(self, event, image_path=None):
        return Verdict(verified=False)

    def on_reply(self, sender, body):
        self.replies.append((sender, body))
        keyword = body.strip().upper()
        if keyword == "DISPATCH":
            return Command(
                command_id="test-cmd",
                issuer_id="hub-sms",
                action="dispatch",
                parameters={"source": "sms_reply"},
            )
        return None

    def reply_for(self, sender, body):
        if body.strip().upper() == "DISPATCH":
            return None
        return f"ack: {body}"


class _StubMQTT:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish_command(self, device_id, command):
        self.published.append((device_id, command))
        return True


class _StubSMS:
    def __init__(self):
        self.sent: list = []
        self.replies: list[tuple[str, str, str]] = []
        self._activity = [{"direction": "out", "content": "hi", "status": "sent"}]
        self._suppressed = False

    def send(self, notification):
        self.sent.append(notification)
        return True

    def record_reply(self, sender, body, action):
        self.replies.append((sender, body, action))

    def recent_activity(self, limit=50):
        return self._activity[:limit]


@pytest.fixture(autouse=True)
def fresh_events():
    default_store.clear()
    yield
    default_store.clear()


@pytest.fixture
def app_client():
    policy = _StubPolicy()
    mqtt = _StubMQTT()
    sms = _StubSMS()
    app = Flask(__name__)
    app.register_blueprint(create_sms_blueprint(policy=policy, mqtt=mqtt, sms=sms))
    app.config["TESTING"] = True
    return app.test_client(), policy, mqtt, sms


def test_dispatch_reply_publishes_to_the_latest_known_device(app_client):
    client, policy, mqtt, sms = app_client
    default_store.note_node("unoq-01")

    resp = client.post("/sms", data={"From": "+15551234567", "Body": "dispatch"})

    assert resp.status_code == 200
    assert len(mqtt.published) == 1
    device_id, command_wire = mqtt.published[0]
    assert device_id == "unoq-01"
    assert command_wire["action"] == "dispatch"
    assert command_wire["parameters"]["source"] == "sms_reply"
    assert sms.replies == [("+15551234567", "dispatch", "mqtt_published")]
    assert policy.replies == [("+15551234567", "dispatch")]


def test_dispatch_reply_with_no_known_device_is_ignored(app_client):
    client, policy, mqtt, sms = app_client

    resp = client.post("/sms", data={"From": "+15551234567", "Body": "dispatch"})

    assert resp.status_code == 200
    assert mqtt.published == []
    assert sms.replies == [("+15551234567", "dispatch", "ignored")]


def test_stop_reply_is_recorded_as_suppressed(app_client):
    client, policy, mqtt, sms = app_client

    client.post("/sms", data={"From": "+15551234567", "Body": "STOP"})

    assert sms.replies == [("+15551234567", "STOP", "suppressed")]


def test_reply_for_text_is_sent_via_sms(app_client):
    client, policy, mqtt, sms = app_client

    client.post("/sms", data={"From": "+15551234567", "Body": "hello there"})

    assert len(sms.sent) == 1
    assert sms.sent[0].message == "ack: hello there"
    assert sms.sent[0].recipient == "+15551234567"


def test_sms_activity_reports_recent_activity_and_suppressed_state(app_client):
    client, policy, mqtt, sms = app_client

    resp = client.get("/user/sms_activity")
    body = resp.get_json()

    assert resp.status_code == 200
    assert body["count"] == 1
    assert body["suppressed"] is False
    assert body["activity"] == sms._activity
