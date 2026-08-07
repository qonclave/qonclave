# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
# SPDX-License-Identifier: MPL-2.0

"""
test_buzzer_app.py — Integration tests for Hub Buzzer command endpoint and BuzzerAlertPolicy.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# Ensure hub directory is on sys.path
HUB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)

from framework.mqtt_bus import MQTTBus
from framework.server import create_app
from framework.vlm import VLMBackend
from apps.security.egress.twilio_sms import SMSBus
from apps.security.policy import SecurityPolicy


class TestBuzzerApp(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.vlm = MagicMock(spec=VLMBackend)
        self.vlm.status.return_value = {"available": False}

        self.mqtt = MagicMock(spec=MQTTBus)
        self.mqtt.status.return_value = {"available": True, "enabled": True}
        self.mqtt.is_available.return_value = True
        self.mqtt.publish_command.return_value = True

        self.sms = MagicMock(spec=SMSBus)
        self.sms.status.return_value = {"available": False}

        self.policy = SecurityPolicy(vlm=self.vlm)
        self.app = create_app(
            policy=self.policy,
            vlm=self.vlm,
            mqtt=self.mqtt,
            sms=self.sms,
            static_dir=self.tmpdir.name,
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_buzzer_command_start_success(self):
        payload = {
            "device_id": "buzzer-01",
            "action": "start",
            "frequency": 880,
            "duration": 500
        }
        res = self.client.post("/user/buzzer-command", json=payload)
        self.assertEqual(res.status_code, 200)

        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["device_id"], "buzzer-01")
        self.assertEqual(data["command"], {
            "type": "buzzer",
            "action": "start",
            "frequency": 880,
            "duration": 500
        })

        self.mqtt.publish_command.assert_called_once_with("buzzer-01", {
            "type": "buzzer",
            "action": "start",
            "frequency": 880,
            "duration": 500
        })

    def test_buzzer_command_stop_success(self):
        payload = {
            "device_id": "buzzer-02",
            "action": "stop"
        }
        res = self.client.post("/user/buzzer-command", json=payload)
        self.assertEqual(res.status_code, 200)

        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["device_id"], "buzzer-02")
        self.assertEqual(data["command"]["action"], "stop")

        self.mqtt.publish_command.assert_called_once_with("buzzer-02", {
            "type": "buzzer",
            "action": "stop",
            "frequency": 440,
            "duration": 0
        })

    def test_buzzer_command_believer_success(self):
        payload = {
            "device_id": "unoq-01",
            "action": "believer"
        }
        res = self.client.post("/user/buzzer-command", json=payload)
        self.assertEqual(res.status_code, 200)

        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["device_id"], "unoq-01")
        self.assertEqual(data["command"]["action"], "believer")

    def test_buzzer_command_invalid_action(self):
        res = self.client.post("/user/buzzer-command", json={"action": "dance"})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("action must be 'start', 'stop', 'tone', 'believer', or 'song'", data["error"])

    def test_buzzer_command_invalid_frequency(self):
        res = self.client.post("/user/buzzer-command", json={"action": "start", "frequency": -10})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("frequency must be between 20 and 20000 Hz", data["error"])

    def test_buzzer_command_mqtt_unavailable(self):
        self.mqtt.publish_command.return_value = False
        res = self.client.post("/user/buzzer-command", json={"action": "start"})
        self.assertEqual(res.status_code, 503)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("MQTT broker unavailable", data["error"])


if __name__ == "__main__":
    unittest.main()
