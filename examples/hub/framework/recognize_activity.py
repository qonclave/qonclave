"""
recognize_activity.py -- in-memory ring buffer of recent POST /recognize
calls, so the dashboard can show what's actually arriving at the hub for
per-track face recognition.

Distinct from events.py's /edge/event ring buffer: /recognize deliberately
bypasses Policy/events entirely (see framework/server.py), and its crops are
deleted right after inference -- this buffer is the one place a crop is kept
around briefly, purely for the dashboard, capped at MAX_ENTRIES so memory use
can never grow unbounded.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from typing import Any

from . import transport

MAX_ENTRIES = 30

_lock = threading.Lock()
_entries: "deque[dict[str, Any]]" = deque(maxlen=MAX_ENTRIES)
_next_id = itertools.count(1)


def record(track_id: int, identity: str, confidence: float, status: str,
           latency_ms: float, image_bytes: bytes, source_ip: str | None = None) -> None:
    """Append one /recognize call to the buffer. Oldest entries are evicted
    automatically once MAX_ENTRIES is exceeded (deque maxlen)."""
    entry = {
        "id": next(_next_id),
        "track_id": track_id,
        "identity": identity,
        "confidence": confidence,
        "status": status,
        "latency_ms": round(latency_ms, 1),
        "source_ip": source_ip,
        "received_at": transport.now_iso(),
        "image": image_bytes,
    }
    with _lock:
        _entries.append(entry)


def recent(limit: int = 20) -> list:
    """Metadata only (no image bytes), newest first."""
    with _lock:
        items = list(_entries)[-limit:]
    items.reverse()
    return [{k: v for k, v in e.items() if k != "image"} for e in items]


def get_image(entry_id: int) -> "bytes | None":
    """The raw JPEG bytes for one entry, or None if it's been evicted."""
    with _lock:
        for e in _entries:
            if e["id"] == entry_id:
                return e["image"]
    return None
