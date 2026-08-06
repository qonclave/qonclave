"""
mqtt_bus.py — hub->edge push channel for the Qonclave framework, now built
on qonclave.transport.mqtt.

/edge/event is a synchronous request/response: an edge device gets a
command back only if it happens to have an HTTP request open at that
moment. MQTT gives the hub a channel to push a command to a device at any
time, independent of that request cycle.

Use-case agnostic: this module knows nothing about what a "command" means
(navigate_to, capture_now, ...) — it just publishes whatever JSON dict a
Policy hands it, namespaced by device_id.

The raw connect/publish/subscribe mechanics are `qonclave.transport.mqtt.
MQTTTransport`, a generic bytes-in/bytes-out PubSubTransport. This module
wraps it with everything that's hub-specific: JSON encoding, a
received-message ring buffer for /test/hub, dual-topic legacy publishing
during the spec migration, and feeding status-topic sightings into the
network page's registry.

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
import datetime as _dt
import json
import logging
import os
import threading

from qonclave.transport.mqtt import MQTTTransport

from . import device_registry

log = logging.getLogger("qonclave.mqtt")

# Publish commands to the pre-spec device-scoped topic as well as the spec one.
# Set to 0 once every edge device in the fleet subscribes to the spec topic;
# see the topic block below for why this is a switch rather than a code edit.
LEGACY_TOPICS = os.environ.get("QONCLAVE_MQTT_LEGACY_TOPICS", "1") == "1"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_CLIENT_ID = "qonclave-hub"
MESSAGES_MAX = 100


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
    """Hub-specific facade over qonclave.transport.mqtt.MQTTTransport."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 client_id: str = DEFAULT_CLIENT_ID, enabled: bool = True):
        self._transport = MQTTTransport(host=host, port=port, client_id=client_id,
                                        enabled=enabled)
        self._lock = threading.Lock()
        self._messages: "collections.deque[dict]" = collections.deque(maxlen=MESSAGES_MAX)

    @property
    def host(self) -> str:
        return self._transport.host

    @property
    def port(self) -> int:
        return self._transport.port

    @property
    def enabled(self) -> bool:
        return self._transport.enabled

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        return self._transport.is_available()

    def status(self) -> dict:
        return self._transport.status()

    def connect(self) -> bool:
        return self._transport.connect()

    def _on_message(self, topic: str, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:
            text = repr(payload)
        entry = {
            "topic": topic,
            "payload": text,
            "received_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        with self._lock:
            self._messages.appendleft(entry)
        # A status message carries the node id in its topic; feed the network
        # page's registry. No-op for topics that aren't status topics.
        device_registry.record_mqtt_topic(topic)
        log.info("MQTT received on %s: %s", topic, text)

    # --- publish --------------------------------------------------------------
    def publish(self, topic: str, payload: dict) -> bool:
        """
        Publish a dict as JSON to an arbitrary topic. Returns True if handed
        to the broker; False (logged) if MQTT is unavailable. Never raises.
        """
        return self._transport.publish(topic, json.dumps(payload).encode("utf-8"))

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
        self._transport.subscribe(topic_filter, self._on_message)
        return True

    def recent_messages(self, limit: int = 50) -> list[dict]:
        """Recently received messages, newest first."""
        with self._lock:
            return list(self._messages)[:limit]

    def close(self):
        self._transport.close()
