"""
mqtt_bus.py — hub->edge push channel for the Qonclave framework.

/edge/event is a synchronous request/response: an edge device gets a
command back only if it happens to have an HTTP request open at that
moment. MQTT gives the hub a channel to push a command to a device at any
time, independent of that request cycle.

Use-case agnostic: this module knows nothing about what a "command" means
(navigate_to, capture_now, ...) — it just publishes whatever JSON dict a
Policy hands it, namespaced by device_id.

The paho client lives here, not in the SDK: CONVENTIONS.md is explicit that
`transport/` holds the `Transport`/`PubSubTransport` ABCs and scheme registry
only -- "somebody else's library for reaching somebody else's process" is the
app's (or, as here, the reference hub's) choice, not the framework's. An
earlier pass (2026-08-06) built a `qonclave.transport.mqtt.MQTTTransport`
wrapping paho directly in the SDK; that was wrong the same way the doc's own
table says it already got this wrong twice before, and has been reverted --
see CONVENTIONS.md's note on this file.

Topics (spec/v1/asyncapi/commands.yaml):
    qonclave/commands/<node_id>    hub -> edge   (JSON)
    qonclave/status/<node_id>      edge -> hub   (reserved, not consumed yet)

Pre-spec topics, still published during the migration — see the topic block
below for why, and how to turn them off:
    qonclave/<device_id>/command
    qonclave/<device_id>/status

Public API:
    bus = MQTTBus(host, port)         # cheap; does not connect yet
    bus.connect()                     # best-effort; False if broker unreachable
    bus.is_available()
    bus.publish(topic, payload)               # -> bool
    bus.publish_command(device_id, command)   # -> bool (thin wrapper over publish())
    bus.subscribe(topic_filter)                # -> bool; idempotent
    bus.recent_messages(limit=50)              # -> list of received messages, newest first
    bus.status()                      # for /health, mirrors VLMBackend.status()

Like VLMBackend, this never raises for the caller: if no broker is running,
the hub keeps serving HTTP/dashboard traffic and publish_command() is a
logged no-op.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
import datetime as _dt

from . import device_registry

log = logging.getLogger("qonclave.mqtt")

# Publish commands to the pre-spec device-scoped topic as well as the spec one.
# Set to 0 once every edge device in the fleet subscribes to the spec topic;
# see the topic block below for why this is a switch rather than a code edit.
LEGACY_TOPICS = os.environ.get("QONCLAVE_MQTT_LEGACY_TOPICS", "1") == "1"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_CLIENT_ID = "qonclave-hub"
CONNECT_TIMEOUT_S = 3
MESSAGES_MAX = 100
# How long to keep a failed connect() cached before allowing another attempt.
# Without this, a broker that isn't up yet at hub-startup (e.g. still binding
# its port) would be marked unavailable for the rest of the process's life.
RECONNECT_COOLDOWN_S = 5


# --- Topics -----------------------------------------------------------------
# Two layouts exist during the migration to spec/v1.
#
# The spec (spec/v1/asyncapi/commands.yaml) groups by FUNCTION:
#     qonclave/commands/<node_id>
# The original demo grouped by DEVICE:
#     qonclave/<device_id>/command
#
# The spec is the standard, so the demo moves. But an edge device is flashed
# firmware and cannot be updated in lockstep with this laptop, so the hub
# publishes to BOTH for one release. Order matters and the obvious order is
# wrong: switching the hub first makes an un-reflashed device silently deaf,
# because nothing errors when you publish to a topic nobody subscribes to.
#
#   1. hub dual-publishes          <- we are here
#   2. edge flashes, moving its single subscription to the spec topic
#   3. QONCLAVE_MQTT_LEGACY_TOPICS=0, then this code is deleted
#
# The device must never subscribe to both while the hub publishes to both: it
# would receive every command twice, and a doubled robot_move turns 60 degrees
# instead of 30. Single subscription throughout; dual publication only here.

def command_topic(node_id: str) -> str:
    """Spec topic — spec/v1/asyncapi/commands.yaml."""
    return f"qonclave/commands/{node_id}"


def status_topic(node_id: str) -> str:
    """Spec topic — spec/v1/asyncapi/commands.yaml."""
    return f"qonclave/status/{node_id}"


def legacy_command_topic(device_id: str) -> str:
    """Pre-spec device-scoped topic. Remove once every device is reflashed."""
    return f"qonclave/{device_id}/command"


def legacy_status_topic(device_id: str) -> str:
    """Pre-spec device-scoped topic. Remove once every device is reflashed."""
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
        self._last_attempt: _dt.datetime | None = None
        self._lock = threading.Lock()
        self._subscriptions: set[str] = set()
        self._messages: "collections.deque[dict]" = collections.deque(maxlen=MESSAGES_MAX)

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if self._client is not None:
            return True
        return self.connect()

    def status(self) -> dict:
        return {
            "available": self._client is not None,
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "connect_attempted": self._last_attempt is not None,
            "connect_error": self._connect_error,
        }

    # --- internal -----------------------------------------------------------
    def connect(self) -> bool:
        with self._lock:
            if self._client is not None:
                return True
            now = _dt.datetime.now(_dt.timezone.utc)
            if (self._last_attempt is not None and
                    (now - self._last_attempt).total_seconds() < RECONNECT_COOLDOWN_S):
                return False
            self._last_attempt = now

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
                client.on_message = self._on_message
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

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = repr(msg.payload)
        entry = {
            "topic": msg.topic,
            "payload": payload,
            "received_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        with self._lock:
            self._messages.appendleft(entry)
        # A status message carries the node id in its topic; feed the network
        # page's registry. No-op for topics that aren't status topics.
        device_registry.record_mqtt_topic(msg.topic)
        log.info("MQTT received on %s: %s", msg.topic, payload)

    # --- publish --------------------------------------------------------------
    def publish(self, topic: str, payload: dict) -> bool:
        """
        Publish a dict as JSON to an arbitrary topic. Returns True if handed
        to the broker; False (logged) if MQTT is unavailable. Never raises.
        """
        if not self.is_available():
            log.warning("Skipping MQTT publish to %s (broker unavailable: %s)",
                        topic, self._connect_error)
            return False

        try:
            body = json.dumps(payload)
            info = self._client.publish(topic, body, qos=1)
            info.wait_for_publish(timeout=CONNECT_TIMEOUT_S)
            log.info("Published to %s: %s", topic, body)
            return True
        except Exception as e:
            log.warning("MQTT publish to %s failed: %s", topic, e)
            return False

    def publish_command(self, device_id: str, command: dict) -> bool:
        """Publish a command to this device, on every enabled topic layout.

        Returns True only if EVERY enabled publish succeeded. Both go to the
        same broker so in practice they succeed or fail together, but a partial
        success is a device that may not have been reached — reporting that as
        True would let /user/robot-command answer 200 for a command that never
        arrived.
        """
        results = [self.publish(command_topic(device_id), command)]
        if LEGACY_TOPICS:
            results.append(self.publish(legacy_command_topic(device_id), command))
        return all(results)

    # --- subscribe / receive ---------------------------------------------------
    def subscribe(self, topic_filter: str) -> bool:
        """
        Subscribe to a topic filter; incoming messages land in the ring
        buffer read by recent_messages(). Idempotent — safe to call on every
        poll. Never raises.
        """
        if not self.is_available():
            return False
        if topic_filter in self._subscriptions:
            return True
        try:
            self._client.subscribe(topic_filter, qos=1)
            self._subscriptions.add(topic_filter)
            log.info("Subscribed to %s", topic_filter)
            return True
        except Exception as e:
            log.warning("MQTT subscribe to %s failed: %s", topic_filter, e)
            return False

    def recent_messages(self, limit: int = 50) -> list[dict]:
        """Recently received messages, newest first."""
        with self._lock:
            return list(self._messages)[:limit]

    def close(self):
        with self._lock:
            if self._client is not None:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
                self._client = None
