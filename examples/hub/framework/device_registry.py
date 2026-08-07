"""
device_registry.py — who is on the network, as seen from the hub, now
supplied by the qonclave SDK.

The passive sighting ledger itself (record/snapshot/state ageing) is
framework, not application, so it now lives in `qonclave.discovery.registry`
and is re-exported here — under this file's historical `device_id`
vocabulary, since that's the field name `/user/devices` and network.html
already speak — while `hub/` converges on `framework/`.

Distance measurement stays here: how you measure "how far is this device" is
deployment-specific (ICMP ping needs a subprocess and OS privileges), so
`start_rtt_prober()` is real hub code that reads the SDK registry's
`probe_targets()` and writes results back via `record_rtt()`.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time

from qonclave.discovery import registry as _registry

log = logging.getLogger("qonclave.devices")

# Env-configurable ageing thresholds. Set on the SDK registry itself (a
# module-level global _state() reads fresh on every call), not merely mirrored
# here, so QONCLAVE_DEVICE_ONLINE_S actually changes snapshot() behavior.
_registry.ONLINE_S = int(os.environ.get("QONCLAVE_DEVICE_ONLINE_S", "60"))
_registry.OFFLINE_S = int(os.environ.get("QONCLAVE_DEVICE_OFFLINE_S", "600"))
ONLINE_S = _registry.ONLINE_S
OFFLINE_S = _registry.OFFLINE_S

# RTT probing, for the network map's distance axis. Only devices that already
# announced themselves are pinged — this is measuring peers, not scanning.
RTT_PROBE_ENABLED = os.environ.get("QONCLAVE_RTT_PROBE_ENABLED", "1") == "1"
RTT_PROBE_INTERVAL_S = int(os.environ.get("QONCLAVE_RTT_PROBE_INTERVAL_S", "10"))

_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE)


def record(device_id: str | None = None, ip: str | None = None,
           source: str = "unknown") -> None:
    _registry.record(node_id=device_id, ip=ip, source=source)


record_mqtt_topic = _registry.record_mqtt_topic
clear = _registry.clear


def snapshot() -> list[dict]:
    """One row per device, most recently seen first. Same shape as the SDK
    registry's, with `node_id` renamed to `device_id` for every existing
    caller of this endpoint (server.py's /user/devices, network.html)."""
    rows = _registry.snapshot()
    for row in rows:
        row["device_id"] = row.pop("node_id")
    return rows


def _ping_once(ip: str) -> float | None:
    """One system ping; RTT in ms, or None if unreachable. Uses the ping
    binary rather than raw ICMP sockets, which need privileges on Linux."""
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", "1000", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    try:
        t0 = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        elapsed_ms = (time.monotonic() - t0) * 1000
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    # Prefer ping's own figure — the subprocess round trip includes process
    # spawn time, which on a sub-millisecond LAN dwarfs the real RTT.
    m = _RTT_RE.search(proc.stdout)
    return float(m.group(1)) if m else round(elapsed_ms, 1)


def start_rtt_prober() -> None:
    """Background thread: periodically ping every known device IP and store
    the RTT — the distance axis of the network map. Daemon, never raises."""
    if not RTT_PROBE_ENABLED:
        log.info("RTT prober disabled (QONCLAVE_RTT_PROBE_ENABLED=0)")
        return

    def _run():
        while True:
            time.sleep(RTT_PROBE_INTERVAL_S)
            for key, ip in _registry.probe_targets():
                _registry.record_rtt(key, _ping_once(ip))

    threading.Thread(target=_run, name="DeviceRTTProber", daemon=True).start()
