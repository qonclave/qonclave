"""
events.py -- ring buffer of recent events, for the operator UI.

Ephemeral operational state only. Historical state belongs to storage/, which is what frees a hub
from maintaining a database (ARCHITECTURE.md section 1). Nothing here survives a restart, and
nothing should: an operator wants the last fifty events, and everything older is the archive's
problem.

`EventStore` is a class rather than the module-level deque it replaces. One hub process has one
store, so the singleton was never wrong in practice — but a module global cannot be instantiated
twice, which leaks state between tests and rules out isolating two tenants in one process.
`default_store` keeps the old import-and-call ergonomics for callers that do not care.

Origin: hub/framework/events.py
"""

from __future__ import annotations

import collections
import os
import threading
from typing import Any

DEFAULT_MAX = int(os.environ.get("QONCLAVE_EVENTS_MAX", "50"))


class EventStore:
    """A bounded, newest-first buffer of recent events."""

    def __init__(self, maxlen: int = DEFAULT_MAX) -> None:
        self._events: collections.deque[dict[str, Any]] = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._latest_frame: str | None = None
        self._latest_node_id: str | None = None
        self.maxlen = maxlen

    def record(self, event: dict[str, Any], frame_name: str | None = None) -> None:
        """Append one event.

        `frame_name` and the node id are tracked as they arrive rather than derived on read,
        because the newest event does not necessarily carry either — a payload-free event has no
        frame, and reading "the latest frame" off it would blank the dashboard's image.
        """
        with self._lock:
            self._events.appendleft(event)
            if frame_name:
                self._latest_frame = frame_name
            node_id = event.get("source_node_id") or event.get("device_id")
            if node_id:
                self._latest_node_id = node_id

    def note_node(self, node_id: str | None) -> None:
        """Update the latest-seen node without recording a full event.

        For samples that skip record() entirely — a /track/analyze crop has no
        edge event, only a track_id — but should still keep latest_node_id()
        fresh so an operator action with no explicit target (an SMS reply, a
        dashboard command) still reaches the right device.
        """
        if node_id:
            with self._lock:
                self._latest_node_id = node_id

    def recent(self, limit: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
        with self._lock:
            return list(self._events)[: (limit or self.maxlen)], self._latest_frame

    def latest_frame_name(self) -> str | None:
        with self._lock:
            return self._latest_frame

    def latest_node_id(self) -> str | None:
        """The node most recently heard from.

        Used to address a command the operator did not explicitly target — an SMS reply, say. In a
        single-device deployment that is unambiguous; in a fleet it is a guess, which is why
        anything that can name its target should.
        """
        with self._lock:
            return self._latest_node_id

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._latest_frame = None
            self._latest_node_id = None


default_store = EventStore()
"""The store a single-process hub uses. An app needing isolation constructs its own."""
