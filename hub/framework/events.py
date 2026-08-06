"""
events.py — generic event store for the Qonclave framework.

An in-memory ring buffer of recent hub-verified events, used by any app's
dashboard/monitoring UI. Use-case agnostic: callers decide what goes into
each event dict.
"""

from __future__ import annotations

import collections
import os
import threading

SCHEMA_VERSION = "0.1"

EVENTS_MAX = int(os.environ.get("QONCLAVE_EVENTS_MAX", "50"))
_events: "collections.deque[dict]" = collections.deque(maxlen=EVENTS_MAX)
_events_lock = threading.Lock()
_latest_frame: dict = {"name": None}
_latest_device: dict = {"id": None}


def record_event(event: dict, frame_name: str | None):
    with _events_lock:
        _events.appendleft(event)
        if frame_name:
            _latest_frame["name"] = frame_name
        device_id = event.get("device_id")
        if device_id:
            _latest_device["id"] = device_id


def note_device(device_id: str | None):
    """Record the most recently seen edge device outside of record_event().
    Lets samples that skip /edge/event (e.g. /track/analyze) keep the hub's
    notion of "the device" fresh so MQTT commands have a target."""
    if device_id:
        with _events_lock:
            _latest_device["id"] = device_id


def recent_events(limit: int | None = None) -> tuple[list[dict], str | None]:
    with _events_lock:
        items = list(_events)[: (limit or EVENTS_MAX)]
        return items, _latest_frame["name"]


def latest_frame_name() -> str | None:
    with _events_lock:
        return _latest_frame["name"]


def latest_device_id() -> str | None:
    with _events_lock:
        return _latest_device["id"]
