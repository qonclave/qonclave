"""
registry.py -- who has announced themselves, and when.

A generic sighting ledger: a lock-guarded, in-memory, ephemeral map of every node that has
announced itself -- a discovery probe, an inbound event, an authenticated data sample, or a status
message on its MQTT topic (`qonclave/status/<node_id>`, spec/v1/asyncapi/commands.yaml). Nothing
here scans a network; a row only exists because a node spoke up. Absence from this ledger means
"never spoke to us", not "not plugged in" -- the same privacy stance the rest of discovery/ takes.

Distinct from peers.py/health.py: those feed the placement ladder (which nodes are federation-
authorized HUB-tier candidates, and whether their heartbeat is current enough to still be one).
This module answers a plainer question -- "what has this deployment ever heard from" -- for an
operator-facing view (a network page, `qonclave doctor`, a log line), independent of placement.

Two kinds of sighting:
    * identified -- the node told us its id. Keyed by that id.
    * anonymous  -- only an IP is known. Keyed by "ip:<addr>" until an identified sighting from
      the same IP absorbs it; one physical node should be one row, not two.

Distance measurement (RTT or anything else) is deliberately NOT here: how you measure "how far is
this node" is deployment-specific (ICMP ping needs a subprocess and OS privileges that don't make
sense on every binding). `probe_targets()`/`record_rtt()` are the seam a caller measures through.

Origin: hub/framework/device_registry.py
"""

from __future__ import annotations

import threading
import time
from typing import Any

# A node is "online" if heard from within ONLINE_S, "idle" until OFFLINE_S, then "offline". Rows
# are kept, not evicted: a node that went quiet is exactly what an observability view exists to
# show. Not configurable here -- threshold tuning is a deployment concern; a caller that wants
# different windows can post-process snapshot()'s age_s itself.
ONLINE_S = 60
OFFLINE_S = 600

_entries: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def record(node_id: str | None = None, ip: str | None = None, source: str = "unknown") -> None:
    """Record one sighting of a node. Either identifier may be absent, but a sighting with
    neither is meaningless and is dropped."""
    node_id = (node_id or "").strip() or None
    ip = (ip or "").strip() or None
    if node_id is None and ip is None:
        return

    now = time.time()
    with _lock:
        if node_id is not None:
            key = node_id
            entry = _entries.get(key)
            if entry is None:
                entry = {"node_id": node_id, "ip": None,
                         "first_seen": now, "sightings": 0, "sources": {}}
                _entries[key] = entry
            if ip is not None:
                entry["ip"] = ip
                # Absorb any anonymous row for the same IP -- it was this node all along, seen
                # before it had introduced itself.
                anon = _entries.pop(f"ip:{ip}", None)
                if anon is not None:
                    entry["first_seen"] = min(entry["first_seen"], anon["first_seen"])
                    entry["sightings"] += anon["sightings"]
                    for src, ts in anon["sources"].items():
                        entry["sources"][src] = max(entry["sources"].get(src, 0), ts)
                    if entry.get("rtt_ms") is None:
                        entry["rtt_ms"] = anon.get("rtt_ms")
        else:
            # Anonymous sighting: credit an identified node already known at this IP rather than
            # opening a second row for it.
            entry = next((e for e in _entries.values()
                          if e["node_id"] is not None and e["ip"] == ip), None)
            if entry is None:
                key = f"ip:{ip}"
                entry = _entries.get(key)
                if entry is None:
                    entry = {"node_id": None, "ip": ip,
                             "first_seen": now, "sightings": 0, "sources": {}}
                    _entries[key] = entry

        entry["last_seen"] = now
        entry["last_source"] = source
        entry["sightings"] += 1
        entry["sources"][source] = now


def record_mqtt_topic(topic: str) -> None:
    """Record a sighting from an MQTT status topic, if the topic carries a node id. Both spec and
    pre-spec layouts are understood: qonclave/status/<node> and qonclave/<node>/status."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "qonclave":
        return
    if parts[1] == "status" and parts[2]:
        record(node_id=parts[2], source="mqtt")
    elif parts[2] == "status" and parts[1] and parts[1] != "status":
        record(node_id=parts[1], source="mqtt")


def _state(last_seen: float, now: float) -> str:
    age = now - last_seen
    if age <= ONLINE_S:
        return "online"
    if age <= OFFLINE_S:
        return "idle"
    return "offline"


def snapshot() -> list[dict[str, Any]]:
    """One row per node, most recently seen first, with a computed state."""
    now = time.time()
    with _lock:
        rows = []
        for entry in _entries.values():
            rows.append({
                "node_id": entry["node_id"],
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


def probe_targets() -> list[tuple[str, str]]:
    """(key, ip) pairs for every non-offline entry with a known IP -- what a distance prober
    should measure. `key` is opaque; pass it back to record_rtt()."""
    now = time.time()
    with _lock:
        return [(key, e["ip"]) for key, e in _entries.items()
                if e["ip"] and _state(e["last_seen"], now) != "offline"]


def record_rtt(key: str, rtt_ms: float | None) -> None:
    """Store a measured round-trip time against the entry addressed by `key` (from
    probe_targets()). A no-op if the entry no longer exists -- it may have been absorbed or the
    ledger cleared between the probe and this call."""
    with _lock:
        entry = _entries.get(key)
        if entry is not None:
            entry["rtt_ms"] = rtt_ms
            entry["rtt_at"] = time.time()


def clear() -> None:
    with _lock:
        _entries.clear()
