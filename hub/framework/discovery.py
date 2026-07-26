"""
discovery.py — Built-in UDP LAN Broadcaster & Discovery Responder for Qonclave Hub.

Broadcasts a periodic JSON heartbeat on UDP port 8888 and responds to LAN discovery
probes from Edge clients, enabling zero-configuration IP discovery without external mDNS libraries.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time

log = logging.getLogger("qonclave.discovery")

DISCOVERY_PORT = 8888
BROADCAST_INTERVAL_SEC = 3.0


def start_broadcaster(http_port: int = 8000, mdns_name: str = "qonclave-hub.local") -> None:
    """Starts background UDP broadcast heartbeat and probe responder thread."""
    def _run():
        time.sleep(1.0)  # Allow server to bind HTTP first
        log.info("Starting built-in LAN UDP Broadcaster on port %d (advertising HTTP port %d)...", DISCOVERY_PORT, http_port)

        # 1. Broadcaster socket (sends heartbeat to 255.255.255.255:8888 every 3s)
        try:
            bcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            bcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception as e:
            log.warning("Could not create UDP broadcast socket: %s", e)
            bcast_sock = None

        # 2. Listener socket (listens on port 8888 for probe requests from new edge devices)
        listen_sock = None
        try:
            listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind(("", DISCOVERY_PORT))
            listen_sock.settimeout(BROADCAST_INTERVAL_SEC)
        except Exception as e:
            log.warning("Could not bind UDP discovery listener on port %d: %s", DISCOVERY_PORT, e)

        payload = json.dumps({
            "service": "qonclave-hub",
            "hostname": mdns_name,
            "port": http_port,
            "version": "1.0"
        }).encode("utf-8")

        while True:
            # Send periodic broadcast
            if bcast_sock:
                try:
                    bcast_sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
                except Exception:
                    pass

            # Listen for incoming probes
            if listen_sock:
                try:
                    data, addr = listen_sock.recvfrom(1024)
                    try:
                        msg = json.loads(data.decode("utf-8", errors="ignore"))
                        if isinstance(msg, dict) and msg.get("probe") in ("qonclave-hub", "any"):
                            log.debug("Received discovery probe from %s; responding with Hub info.", addr)
                            listen_sock.sendto(payload, addr)
                    except Exception:
                        pass
                except socket.timeout:
                    continue
                except Exception:
                    time.sleep(1.0)
            else:
                time.sleep(BROADCAST_INTERVAL_SEC)

    threading.Thread(target=_run, name="HubLANDiscovery", daemon=True).start()
