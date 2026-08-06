"""
hub_client.py — HTTP client for the Qonclave hub's assistant endpoint.

Mirrors the hub discovery pattern from qonclave-detect-objects-on-camera/main.py:
  - Static IP by default
  - Optional mDNS lookup when HUB_DISCOVERY_ENABLED=1
  - Background health monitor thread that fires hub_status WebSocket events

Public API:
    client = HubClient(ui)
    client.start_health_monitor()
    result = client.query(text, device_id)  # blocks, returns dict or raises
"""
from __future__ import annotations

import logging
import os
import threading
import time

import requests

log = logging.getLogger(__name__)

HUB_IP = os.environ.get("HUB_IP", "192.168.18.62")
HUB_PORT = int(os.environ.get("HUB_PORT", "8000"))
HUB_TIMEOUT_SEC = int(os.environ.get("HUB_TIMEOUT_SEC", "30"))
HUB_DISCOVERY_ENABLED = os.environ.get("HUB_DISCOVERY_ENABLED", "0").lower() in ("1", "true", "yes", "on")
HUB_MDNS_NAME = os.environ.get("HUB_MDNS_NAME", "qonclave-hub.local")
_HEALTH_POLL_SEC = 5


class HubClient:
    def __init__(self, ui=None) -> None:
        self._ui = ui
        self._base_url: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Hub discovery
    # ------------------------------------------------------------------
    def _resolve_base_url(self) -> str:
        with self._lock:
            if self._base_url:
                return self._base_url
        if HUB_DISCOVERY_ENABLED:
            url = self._try_mdns()
            if url:
                with self._lock:
                    self._base_url = url
                log.info("Hub discovered via mDNS: %s", url)
                return url
        url = f"http://{HUB_IP}:{HUB_PORT}"
        with self._lock:
            self._base_url = url
        return url

    def _try_mdns(self) -> str | None:
        try:
            import socket
            addr = socket.gethostbyname(HUB_MDNS_NAME)
            return f"http://{addr}:{HUB_PORT}"
        except Exception:
            log.debug("mDNS lookup failed for %s, falling back to static IP", HUB_MDNS_NAME)
            return None

    # ------------------------------------------------------------------
    # Health monitor
    # ------------------------------------------------------------------
    def start_health_monitor(self) -> None:
        t = threading.Thread(target=self._health_loop, daemon=True, name="hub-health")
        t.start()

    def _health_loop(self) -> None:
        while True:
            connected = False
            host = HUB_IP
            port = HUB_PORT
            try:
                url = self._resolve_base_url()
                r = requests.get(f"{url}/health", timeout=3)
                connected = r.status_code == 200
            except Exception:
                pass
            if self._ui:
                try:
                    self._ui.send_message("hub_status", {"connected": connected, "host": host, "port": port})
                except Exception:
                    pass
            time.sleep(_HEALTH_POLL_SEC)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def query(self, text: str, device_id: str) -> dict:
        """POST /assistant/query and return the response dict.
        Raises requests.RequestException on network/HTTP failure."""
        url = self._resolve_base_url()
        endpoint = f"{url}/assistant/query"
        payload = {"query": text, "device_id": device_id}
        log.info("[HUB ] Outgoing transcript: %r", text)
        log.info("[HUB ] POST %s", endpoint)
        t_start = time.monotonic()
        resp = requests.post(
            endpoint,
            json=payload,
            timeout=HUB_TIMEOUT_SEC,
        )
        elapsed_ms = (time.monotonic() - t_start) * 1000
        resp.raise_for_status()
        result = resp.json()
        response_text = result.get("response") if isinstance(result, dict) else None
        if not isinstance(response_text, str) or not response_text.strip():
            raise ValueError(f"Invalid hub response schema: {result!r}")
        log.info("[HUB ] Response in %.0f ms: %r", elapsed_ms, response_text[:160])
        return result
