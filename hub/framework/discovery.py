"""
discovery.py — Built-in UDP LAN Broadcaster & Discovery Responder for
Qonclave Hub, now supplied by the qonclave SDK.

The broadcast/probe-respond mechanism itself is framework, not application,
so it now lives in `qonclave.discovery.announce` (+ the socket-level
`qonclave.discovery.backends.udp`) and is re-exported here while `hub/`
converges on `framework/`.

The announced payload is still this file's historical ad-hoc shape
(`{"service": "qonclave-hub", "hostname": ..., "port": ..., "version": "1.0"}`),
not yet a real spec/v1 `node-manifest.schema.json` document — kept
byte-compatible with edge devices already flashed against it. See
CONVENTIONS.md's note on this migration for the follow-up.
"""

from __future__ import annotations

from qonclave.discovery import announce
from qonclave.discovery.backends.udp import DISCOVERY_PORT, lan_ip  # noqa: F401

MDNS_NAME = "qonclave-hub.local"
BROADCAST_INTERVAL_SEC = announce.BROADCAST_INTERVAL_S


def start_broadcaster(http_port: int = 8000, mdns_name: str = MDNS_NAME) -> None:
    """Starts background UDP broadcast heartbeat and probe responder thread."""
    payload = {
        "service": "qonclave-hub",
        "hostname": mdns_name,
        "port": http_port,
        "version": "1.0",
    }
    announce.start(payload, port=DISCOVERY_PORT, interval_s=BROADCAST_INTERVAL_SEC)
