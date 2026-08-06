"""
events.py — event ring buffer, now supplied by the qonclave SDK.

The buffer itself moved to `qonclave.hub.events.EventStore`; this module keeps
the module-level function API that `server.py` and the dashboard routes already
use, backed by the SDK's default store.

SCHEMA_VERSION stays here. It versions the hub's own RESPONSE envelope — the
`{received, hub_verified, alert, ...}` body an edge device reads back — which is
this deployment's contract, not the wire spec's. spec/v1 versions the documents
that cross between nodes; those carry their own `schema_version` of "1.0".
"""

from __future__ import annotations

from qonclave.hub.events import EventStore, default_store  # noqa: F401

SCHEMA_VERSION = "0.1"

EVENTS_MAX = default_store.maxlen


def record_event(event: dict, frame_name: str | None):
    default_store.record(event, frame_name)


def note_device(device_id: str | None):
    """Record the most recently seen edge device outside of record_event().
    Lets samples that skip /edge/event (e.g. /track/analyze) keep the hub's
    notion of "the device" fresh so MQTT commands have a target."""
    default_store.note_node(device_id)


def recent_events(limit: int | None = None) -> tuple[list[dict], str | None]:
    return default_store.recent(limit)


def latest_frame_name() -> str | None:
    return default_store.latest_frame_name()


def latest_device_id() -> str | None:
    return default_store.latest_node_id()
