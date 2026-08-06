"""
announce.py -- publish this node's manifest.

Spec: COMMUNICATION.md section 1
Origin: the broadcast half of hub/framework/discovery.py

Not yet a real spec/v1 node-manifest.schema.json document -- `payload` here is whatever dict the
caller wants echoed on the wire, kept byte-compatible with hub/framework/discovery.py's existing
edge devices during this migration. See CONVENTIONS.md's note on this file for the follow-up.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from . import registry
from .backends.udp import DISCOVERY_PORT, UDPAnnounceBackend

log = logging.getLogger("qonclave.discovery")

BROADCAST_INTERVAL_S = 3.0


def start(payload: dict, *, port: int = DISCOVERY_PORT,
          interval_s: float = BROADCAST_INTERVAL_S) -> None:
    """Background thread: periodically broadcast `payload` and answer discovery probes with it.

    A probe is accepted if its `probe` field equals this node's own `payload["service"]` value or
    the literal string "any". Every accepted probe also records a sighting via discovery.registry
    -- a probe is a node announcing itself, whether or not it named itself in the probe.
    """
    service = payload.get("service")
    wire = json.dumps(payload).encode("utf-8")

    def _run():
        time.sleep(1.0)  # let the caller finish binding its own listener first
        log.info("Starting UDP discovery announce on port %d (service=%s)...", port, service)
        backend = UDPAnnounceBackend(port=port, recv_timeout=interval_s)
        if not backend.can_broadcast:
            log.warning("Could not create UDP broadcast socket")
        if not backend.can_listen:
            log.warning("Could not bind UDP discovery listener on port %d", port)

        while True:
            backend.broadcast(wire)

            received = backend.poll()
            if received is None:
                continue
            data, addr = received
            try:
                msg = json.loads(data.decode("utf-8", errors="ignore"))
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            if msg.get("probe") in (service, "any"):
                log.debug("Received discovery probe from %s; responding.", addr)
                registry.record(node_id=msg.get("node_id") or msg.get("device_id"),
                               ip=addr[0], source="discovery")
                backend.reply(wire, addr)

    threading.Thread(target=_run, name="QonclaveDiscoveryAnnounce", daemon=True).start()
