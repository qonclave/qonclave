# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
mqtt_client.py — edge-side receiver for the hub->edge push channel.

The hub (framework/mqtt_bus.py) publishes commands to
    qonclave/<device_id>/command
This module is the other end of that channel: it connects to the same MQTT
broker and subscribes to this device's command topic so the hub can push a
command at any time, independent of the /edge/event request cycle.

Scope for now: just a basic, resilient connection + subscription. Received
commands are handed to an optional callback (and always logged). Acting on a
command (drive the motors, capture a frame, ...) is deliberately out of scope
here — that gets layered on later.

Mirrors the hub's MQTTBus design:
  - paho is imported lazily so a missing install never breaks the edge app
  - never raises for the caller; if the broker is unreachable the edge app
    keeps detecting and escalating, and this is a logged no-op
  - reconnects automatically once a broker appears

Public API:
    client = EdgeMQTTClient(device_id, host, port, on_command=fn)  # cheap; no connect
    client.start()          # spawn background connect/loop thread; returns immediately
    client.is_connected()   # -> bool
    client.status()         # dict, for health/UI
    client.close()
"""

from __future__ import annotations

import json
import threading


def command_topic(device_id: str) -> str:
    return f"qonclave/{device_id}/command"


def status_topic(device_id: str) -> str:
    return f"qonclave/{device_id}/status"


class EdgeMQTTClient:
    """Subscribes to this device's hub->edge command topic. Best-effort."""

    def __init__(self, device_id, host="127.0.0.1", port=1883,
                 enabled=True, on_command=None, logger=None):
        self.device_id = device_id
        self.host = host
        self.port = port
        self.enabled = enabled
        self._on_command = on_command
        self._log = logger
        self._client = None
        self._connected = False
        self._connect_error = None
        self._lock = threading.Lock()
        self._thread = None

    # --- logging helpers (Logger has no .debug/.warning parity guarantee) ---
    def _info(self, msg):
        if self._log:
            self._log.info(msg)

    def _warn(self, msg):
        if self._log:
            # Arduino Logger exposes warning(); fall back to info() otherwise.
            (getattr(self._log, "warning", None) or self._log.info)(msg)

    def _error(self, msg):
        if self._log:
            (getattr(self._log, "error", None) or self._log.info)(msg)

    # --- lifecycle ----------------------------------------------------------
    def start(self):
        """Begin connecting in a background thread. Safe to call once."""
        if not self.enabled:
            self._connect_error = "MQTT disabled (EDGE_MQTT_ENABLED=0)"
            self._info("Edge MQTT disabled by config")
            return
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="EdgeMQTTClient", daemon=True)
            self._thread.start()

    def _run(self):
        try:
            # Imported HERE so a missing paho-mqtt install doesn't break the
            # edge app for devices that don't need MQTT.
            import paho.mqtt.client as mqtt  # type: ignore
        except Exception as e:
            self._connect_error = f"could not import paho-mqtt: {e}"
            self._warn(f"Edge MQTT unavailable: {self._connect_error}")
            return

        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"qonclave-edge-{self.device_id}",
            )
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            # reconnect_delay_set makes loop_forever retry a downed broker.
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            self._client = client
            self._info(f"Edge MQTT connecting to {self.host}:{self.port} ...")
            # connect_async + loop_forever: tolerates a broker that isn't up
            # yet and keeps retrying until it appears.
            client.connect_async(self.host, self.port, keepalive=30)
            client.loop_forever(retry_first_connection=True)
        except Exception as e:
            self._connect_error = f"connect to {self.host}:{self.port} failed: {e}"
            self._warn(f"Edge MQTT unavailable: {self._connect_error}")
            self._client = None

    # --- paho callbacks -----------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # reason_code == 0 (or Success) means connected.
        if getattr(reason_code, "is_failure", False):
            self._connect_error = f"connect refused: {reason_code}"
            self._warn(f"Edge MQTT {self._connect_error}")
            return
        self._connected = True
        self._connect_error = None
        topic = command_topic(self.device_id)
        client.subscribe(topic, qos=1)
        self._info(f"Edge MQTT connected; subscribed to {topic}")

    def _on_disconnect(self, client, userdata, *args):
        self._connected = False
        self._info("Edge MQTT disconnected (will retry)")

    def _on_message(self, client, userdata, msg):
        raw = None
        try:
            raw = msg.payload.decode("utf-8", errors="replace")
            command = json.loads(raw)
        except Exception:
            command = {"raw": raw}
        self._info(f"Edge MQTT command on {msg.topic}: {raw}")
        if self._on_command:
            try:
                self._on_command(command)
            except Exception as e:
                self._error(f"on_command handler failed: {e}")

    # --- introspection ------------------------------------------------------
    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "device_id": self.device_id,
            "topic": command_topic(self.device_id),
            "connect_error": self._connect_error,
        }

    def close(self):
        client = self._client
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
        self._client = None
        self._connected = False
