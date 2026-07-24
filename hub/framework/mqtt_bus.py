"""
mqtt_bus.py — hub->edge push channel for the Qonclave framework.

/edge/event is a synchronous request/response: an edge device gets a
command back only if it happens to have an HTTP request open at that
moment. MQTT gives the hub a channel to push a command to a device at any
time, independent of that request cycle.

Use-case agnostic: this module knows nothing about what a "command" means
(navigate_to, capture_now, ...) — it just publishes whatever JSON dict a
Policy hands it, namespaced by device_id.

Topics:
    qonclave/<device_id>/command   hub -> edge   (JSON)
    qonclave/<device_id>/status    edge -> hub   (reserved, not consumed yet)

Public API:
    bus = MQTTBus(host, port)         # cheap; does not connect yet
    bus.connect()                     # best-effort; False if broker unreachable
    bus.is_available()
    bus.publish_command(device_id, command)   # -> bool
    bus.status()                      # for /health, mirrors VLMBackend.status()

Like VLMBackend, this never raises for the caller: if no broker is running,
the hub keeps serving HTTP/dashboard traffic and publish_command() is a
logged no-op.
"""

from __future__ import annotations

import json
import logging
import threading

log = logging.getLogger("qonclave.mqtt")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_CLIENT_ID = "qonclave-hub"
CONNECT_TIMEOUT_S = 3


def command_topic(device_id: str) -> str:
    return f"qonclave/{device_id}/command"


def status_topic(device_id: str) -> str:
    return f"qonclave/{device_id}/status"


class MQTTBus:
    """Lazily connects to a Mosquitto (or any MQTT 3.1.1/5) broker."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 client_id: str = DEFAULT_CLIENT_ID, enabled: bool = True):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.enabled = enabled
        self._client = None
        self._connect_error: str | None = None
        self._connect_attempted = False
        self._lock = threading.Lock()

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if self._client is not None:
            return True
        if self._connect_attempted:
            return False
        return self.connect()

    def status(self) -> dict:
        return {
            "available": self._client is not None,
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "connect_attempted": self._connect_attempted,
            "connect_error": self._connect_error,
        }

    # --- internal -----------------------------------------------------------
    def connect(self) -> bool:
        with self._lock:
            if self._client is not None:
                return True
            if self._connect_attempted:
                return False
            self._connect_attempted = True

            if not self.enabled:
                self._connect_error = "MQTT disabled (QONCLAVE_MQTT_ENABLED=0)"
                log.info("MQTT disabled by config")
                return False

            try:
                # Imported HERE (not at module top) so a missing paho-mqtt
                # install doesn't break hubs that don't need MQTT.
                import paho.mqtt.client as mqtt  # type: ignore
            except Exception as e:
                self._connect_error = f"could not import paho-mqtt: {e}"
                log.warning("MQTT unavailable: %s", self._connect_error)
                return False

            try:
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id,
                )
                client.connect(self.host, self.port, keepalive=30)
                client.loop_start()
                self._client = client
                log.info("Connected to MQTT broker at %s:%s", self.host, self.port)
                return True
            except Exception as e:
                self._connect_error = f"connect to {self.host}:{self.port} failed: {e}"
                log.warning("MQTT unavailable: %s", self._connect_error)
                self._client = None
                return False

    # --- publish --------------------------------------------------------------
    def publish_command(self, device_id: str, command: dict) -> bool:
        """
        Publish a command dict to qonclave/<device_id>/command. Returns True
        if the publish was handed to the broker; False (logged) if MQTT is
        unavailable. Never raises for the caller.
        """
        if not self.is_available():
            log.warning("Skipping MQTT publish to device=%s (broker unavailable: %s)",
                        device_id, self._connect_error)
            return False

        topic = command_topic(device_id)
        try:
            payload = json.dumps(command)
            info = self._client.publish(topic, payload, qos=1)
            info.wait_for_publish(timeout=CONNECT_TIMEOUT_S)
            log.info("Published command to %s: %s", topic, payload)
            return True
        except Exception as e:
            log.warning("MQTT publish to %s failed: %s", topic, e)
            return False

    def close(self):
        with self._lock:
            if self._client is not None:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
                self._client = None
