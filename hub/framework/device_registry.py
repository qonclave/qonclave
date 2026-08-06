"""
device_registry.py — who is on the network, as seen from the hub.

Mirrors `framework/events.py` / `framework/track_store.py`: module-level,
lock-guarded, ephemeral. The hub never scans the subnet — that would be the
kind of reaching-out the privacy cascade avoids — it only records devices that
announce themselves: a UDP discovery probe, an /edge/event post, a
/track/analyze crop, or an MQTT status message. Absence from this registry
therefore means "never spoke to us", not "not plugged in".

Two kinds of sighting:
    * identified — the device told us its id (event source_node_id, MQTT
      status topic). Keyed by that id.
    * anonymous  — only an IP is known (discovery probe, /track/analyze).
      Keyed by "ip:<addr>" until an identified sighting from the same IP
      absorbs it; one physical device should be one row, not two.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger("qonclave.devices")

# A device is "online" if heard from within ONLINE_S, "idle" until OFFLINE_S,
# then "offline". Rows are kept, not evicted: a device that went quiet is
# exactly what a network page exists to show.
ONLINE_S = int(os.environ.get("QONCLAVE_DEVICE_ONLINE_S", "60"))
OFFLINE_S = int(os.environ.get("QONCLAVE_DEVICE_OFFLINE_S", "600"))

# RTT probing, for the network map's distance axis. Only devices that already
# announced themselves are pinged — this is measuring peers, not scanning.
RTT_PROBE_ENABLED = os.environ.get("QONCLAVE_RTT_PROBE_ENABLED", "1") == "1"
RTT_PROBE_INTERVAL_S = int(os.environ.get("QONCLAVE_RTT_PROBE_INTERVAL_S", "10"))

_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE)

_devices: dict[str, dict] = {}
_lock = threading.Lock()


def record(device_id: str | None = None, ip: str | None = None,
           source: str = "unknown") -> None:
    """Record one sighting of a device. Either identifier may be absent,
    but a sighting with neither is meaningless and is dropped."""
    device_id = (device_id or "").strip() or None
    ip = (ip or "").strip() or None
    if device_id is None and ip is None:
        return

    now = time.time()
    with _lock:
        if device_id is not None:
            key = device_id
            entry = _devices.get(key)
            if entry is None:
                entry = {"device_id": device_id, "ip": None,
                         "first_seen": now, "sightings": 0, "sources": {}}
                _devices[key] = entry
            if ip is not None:
                entry["ip"] = ip
                # Absorb any anonymous row for the same IP — it was this
                # device all along, seen before it had introduced itself.
                anon = _devices.pop(f"ip:{ip}", None)
                if anon is not None:
                    entry["first_seen"] = min(entry["first_seen"], anon["first_seen"])
                    entry["sightings"] += anon["sightings"]
                    for src, ts in anon["sources"].items():
                        entry["sources"][src] = max(entry["sources"].get(src, 0), ts)
                    if entry.get("rtt_ms") is None:
                        entry["rtt_ms"] = anon.get("rtt_ms")
        else:
            # Anonymous sighting: credit an identified device already known at
            # this IP rather than opening a second row for it.
            entry = next((e for e in _devices.values()
                          if e["device_id"] is not None and e["ip"] == ip), None)
            if entry is None:
                key = f"ip:{ip}"
                entry = _devices.get(key)
                if entry is None:
                    entry = {"device_id": None, "ip": ip,
                             "first_seen": now, "sightings": 0, "sources": {}}
                    _devices[key] = entry

        entry["last_seen"] = now
        entry["last_source"] = source
        entry["sightings"] += 1
        entry["sources"][source] = now


def record_mqtt_topic(topic: str) -> None:
    """Record a sighting from an MQTT status topic, if the topic carries a
    node id. Both layouts from mqtt_bus's migration are understood:
    qonclave/status/<node> (spec) and qonclave/<node>/status (pre-spec)."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "qonclave":
        return
    if parts[1] == "status" and parts[2]:
        record(device_id=parts[2], source="mqtt")
    elif parts[2] == "status" and parts[1] and parts[1] != "status":
        record(device_id=parts[1], source="mqtt")


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
            with _lock:
                targets = [(key, e["ip"]) for key, e in _devices.items()
                           if e["ip"] and _state(e["last_seen"], time.time()) != "offline"]
            for key, ip in targets:
                rtt = _ping_once(ip)
                with _lock:
                    entry = _devices.get(key)
                    if entry is not None:
                        entry["rtt_ms"] = rtt
                        entry["rtt_at"] = time.time()

    threading.Thread(target=_run, name="DeviceRTTProber", daemon=True).start()


def _state(last_seen: float, now: float) -> str:
    age = now - last_seen
    if age <= ONLINE_S:
        return "online"
    if age <= OFFLINE_S:
        return "idle"
    return "offline"


def snapshot() -> list[dict]:
    """One row per device, most recently seen first, with a computed state."""
    now = time.time()
    with _lock:
        rows = []
        for entry in _devices.values():
            rows.append({
                "device_id": entry["device_id"],
                "ip": entry["ip"],
                "state": _state(entry["last_seen"], now),
                "first_seen": entry["first_seen"],
                "last_seen": entry["last_seen"],
                "age_s": round(now - entry["last_seen"], 1),
                "last_source": entry.get("last_source"),
                "sources": sorted(entry["sources"]),
                "sightings": entry["sightings"],
                "rtt_ms": entry.get("rtt_ms"),
            })
        rows.sort(key=lambda r: r["last_seen"], reverse=True)
        return rows


def clear() -> None:
    with _lock:
        _devices.clear()
