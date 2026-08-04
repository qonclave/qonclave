"""
activity.py — in-memory ring buffer of recent POST /assistant/query calls, so
the hub dashboard can show what the edge device asked and what went back.

Distinct from history.py: that keeps a short per-device conversation for the
model's benefit and is keyed by device_id; this is a flat, newest-first feed
across all devices for a human watching the dashboard, and it records how each
reply was produced (LLM vs template fallback, and why).

Capped at MAX_ENTRIES so memory use can never grow unbounded.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from typing import Any

from framework import transport

MAX_ENTRIES = 30

_lock = threading.Lock()
_entries: "deque[dict[str, Any]]" = deque(maxlen=MAX_ENTRIES)
_next_id = itertools.count(1)


def record(device_id: str, query: str, response: str, tool_used: str | None,
           latency_ms: float, llm_latency_s: float | None = None,
           fallback_reason: str | None = None) -> None:
    """Append one /assistant/query call. Oldest entries are evicted
    automatically once MAX_ENTRIES is exceeded (deque maxlen)."""
    entry = {
        "id": next(_next_id),
        "device_id": device_id,
        "query": query,
        "response": response,
        "tool_used": tool_used,
        # "llm" when the model answered, "template" for a canned reply
        "source": "llm" if tool_used == "llm" else "template",
        "latency_ms": round(latency_ms, 1),
        "llm_latency_s": llm_latency_s,
        # set only when the LLM was tried and could not answer
        "fallback_reason": fallback_reason,
        "received_at": transport.now_iso(),
    }
    with _lock:
        _entries.append(entry)


def recent(limit: int = 20) -> list:
    """Newest first."""
    with _lock:
        items = list(_entries)[-limit:]
    items.reverse()
    return items


def clear() -> None:
    """Drop every entry. Used by tests."""
    with _lock:
        _entries.clear()
