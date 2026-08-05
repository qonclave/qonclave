# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
# SPDX-License-Identifier: MPL-2.0

"""
buzzer_edge.py — Python MQTT client for Edge nodes controlling Arduino Modulino Buzzer.

Subscribes to `qonclave/<device_id>/command` over MQTT and triggers buzzer tone / stop
actions on the local microcontroller or system buzzer.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("qonclave.buzzer_edge")


def command_topic(device_id: str) -> str:
    return f"qonclave/{device_id}/command"


class BuzzerHardware:
    """Interface to drive Arduino Modulino Buzzer via Arduino RouterBridge or serial/GPIO fallback."""

    def __init__(self):
        self._bridge = None
        self._is_active = False
        try:
            # Check for Arduino UNO Q RouterBridge
            from Arduino_RouterBridge import Bridge
            Bridge.begin()
            self._bridge = Bridge
            log.info("Initialized Arduino RouterBridge for Modulino Buzzer hardware")
        except ImportError:
            log.info("Arduino_RouterBridge not available — running in console software buzzer mode")

    def start_tone(self, frequency: int = 440, duration: int = 0):
        self._is_active = True
        log.info(f"🔊 BUZZER START: Frequency={frequency} Hz, Duration={duration if duration > 0 else 'continuous'} ms")
        if self._bridge:
            try:
                self._bridge.call("start_buzzer", f"{frequency}:{duration}")
            except Exception as e:
                log.warning(f"Bridge call start_buzzer failed: {e}")

    def stop_tone(self):
        self._is_active = False
        log.info("🔇 BUZZER STOP: Sound output muted")
        if self._bridge:
            try:
                self._bridge.call("stop_buzzer", "")
            except Exception as e:
                log.warning(f"Bridge call stop_buzzer failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Qonclave Modulino Buzzer Edge Node Client")
    parser.add_argument("--device-id", default=os.environ.get("EDGE_DEVICE_ID", "buzzer-01"),
                        help="Target device ID for MQTT topic (default: buzzer-01)")
    parser.add_argument("--host", default=os.environ.get("QONCLAVE_MQTT_HOST", "127.0.0.1"),
                        help="MQTT broker host IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("QONCLAVE_MQTT_PORT", "1883")),
                        help="MQTT broker port (default: 1883)")
    args = parser.parse_args()

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.error("paho-mqtt library is required. Install via: pip install paho-mqtt")
        sys.exit(1)

    buzzer = BuzzerHardware()
    topic = command_topic(args.device_id)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        log.info(f"Connected to MQTT broker at {args.host}:{args.port}")
        client.subscribe(topic, qos=1)
        log.info(f"Subscribed to topic: {topic}")

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
            data = json.loads(payload)
            log.info(f"Received MQTT message on {msg.topic}: {payload}")

            action = str(data.get("action") or "").lower()
            frequency = int(data.get("frequency", 440))
            duration = int(data.get("duration", 0))

            if action in ("start", "tone"):
                buzzer.start_tone(frequency, duration)
            elif action in ("stop", "notone"):
                buzzer.stop_tone()
            else:
                log.warning(f"Unrecognized buzzer action: '{action}'")

        except Exception as e:
            log.error(f"Error handling message on {msg.topic}: {e}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"qonclave-buzzer-{args.device_id}")
    client.on_connect = on_connect
    client.on_message = on_message

    log.info(f"Connecting to MQTT Broker {args.host}:{args.port} for device '{args.device_id}'...")
    try:
        client.connect(args.host, args.port, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Shutting down buzzer edge client...")
        buzzer.stop_tone()
        client.disconnect()


if __name__ == "__main__":
    main()
