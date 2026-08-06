"""
mqtt.py -- MQTT pub/sub transport.

The push path for devices that can hold a subscription. Devices that cannot -- anything on the
`minimal` profile -- never appear here; their commands accumulate in the hub mailbox instead.

Best-effort by design: an unreachable broker must not fail an event that was otherwise handled.

This is the generic transport primitive only -- bytes in, bytes out, one handler per topic
filter. JSON encoding, a received-message ring buffer, dual-topic legacy publishing during the
spec migration, and wiring a status-topic sighting into discovery.registry are all hub-specific
concerns that stay in hub/framework/mqtt_bus.py's MQTTBus, which wraps this class rather than
being replaced by it.

Spec: spec/v1/asyncapi/commands.yaml
Origin: hub/framework/mqtt_bus.py
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from typing import Callable

from .base import PubSubTransport

log = logging.getLogger("qonclave.transport.mqtt")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_CLIENT_ID = "qonclave"
CONNECT_TIMEOUT_S = 3.0
# How long to keep a failed connect() cached before allowing another attempt. Without this, a
# broker that isn't up yet at startup (e.g. still binding its port) would be marked unavailable
# for the rest of the process's life.
RECONNECT_COOLDOWN_S = 5.0


class MQTTTransport(PubSubTransport):
    """Lazily connects to a Mosquitto (or any MQTT 3.1.1/5) broker via paho-mqtt.

    Best-effort throughout: a missing paho-mqtt install, an unreachable broker, or a failed
    publish/subscribe call all degrade to a logged False/no-op rather than raising -- a caller
    using MQTT as a secondary channel (the primary usually being HTTP) must never be broken by it.
    """

    scheme = "mqtt"

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                client_id: str = DEFAULT_CLIENT_ID, enabled: bool = True,
                connect_timeout_s: float = CONNECT_TIMEOUT_S,
                reconnect_cooldown_s: float = RECONNECT_COOLDOWN_S):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.enabled = enabled
        self.connect_timeout_s = connect_timeout_s
        self.reconnect_cooldown_s = reconnect_cooldown_s
        self._client = None
        self._connect_error: str | None = None
        self._last_attempt: _dt.datetime | None = None
        self._lock = threading.Lock()
        self._subscriptions: set[str] = set()

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

    # --- internal -------------------------------------------------------------
    def connect(self) -> bool:
        with self._lock:
            if self._client is not None:
                return True
            now = _dt.datetime.now(_dt.timezone.utc)
            if (self._last_attempt is not None and
                    (now - self._last_attempt).total_seconds() < self.reconnect_cooldown_s):
                return False
            self._last_attempt = now

            if not self.enabled:
                self._connect_error = "MQTT disabled"
                log.info("MQTT disabled by config")
                return False

            try:
                # Imported HERE (not at module top) so a missing paho-mqtt install doesn't break
                # a caller that doesn't need MQTT.
                import paho.mqtt.client as mqtt  # type: ignore
            except Exception as e:
                self._connect_error = f"could not import paho-mqtt: {e}"
                log.warning("MQTT unavailable: %s", self._connect_error)
                return False

            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
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

    # --- PubSubTransport ------------------------------------------------------
    def publish(self, topic: str, body: bytes, *, qos: int = 1, retain: bool = False) -> bool:
        if not self.is_available():
            log.warning("Skipping MQTT publish to %s (broker unavailable: %s)",
                        topic, self._connect_error)
            return False
        try:
            info = self._client.publish(topic, body, qos=qos, retain=retain)
            info.wait_for_publish(timeout=self.connect_timeout_s)
            log.info("Published to %s (%d bytes)", topic, len(body))
            return True
        except Exception as e:
            log.warning("MQTT publish to %s failed: %s", topic, e)
            return False

    def subscribe(self, topic: str, handler: Callable[[str, bytes], None]) -> None:
        """Register `handler(topic, body)` for every message on `topic` (a filter; may include
        +/#). Idempotent per exact filter string -- a second subscribe() for an already-active
        filter is a no-op, so a caller that re-subscribes on every poll (as a hub's /user/devices
        route does) doesn't round-trip a SUBSCRIBE packet every time. A no-op if the broker is
        unavailable -- the caller gets no messages rather than an exception, the same best-effort
        stance as publish()."""
        if not self.is_available():
            return
        if topic in self._subscriptions:
            return

        def _on_message(client, userdata, msg):
            handler(msg.topic, msg.payload)

        try:
            self._client.message_callback_add(topic, _on_message)
            self._client.subscribe(topic, qos=1)
            self._subscriptions.add(topic)
            log.info("Subscribed to %s", topic)
        except Exception as e:
            log.warning("MQTT subscribe to %s failed: %s", topic, e)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
                self._client = None
