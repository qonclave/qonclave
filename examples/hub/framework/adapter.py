"""
adapter.py — wire-vocabulary translation, now supplied by the qonclave SDK.

This module used to hold the translation between the pre-spec demo vocabulary
and spec/v1. That logic is framework, not application — it is entirely about
what a field is called on the wire — so it now lives in `qonclave.hub.ingest`
and is re-exported here while `hub/` converges on `framework/`.

Every existing import keeps working:

    from framework import adapter
    adapter.to_edge_event(...)

When hub/framework/ is finally absorbed, this file goes away and callers import
qonclave.hub.ingest directly.
"""

from __future__ import annotations

from qonclave.hub.ingest import (  # noqa: F401
    DEFAULT_NODE_ID,
    DEFAULT_TRIGGER,
    LEGACY_TO_METADATA,
    LEGACY_TO_SPEC,
    SPEC_MARKERS,
    SPEC_TO_LEGACY,
    command_from_legacy,
    command_to_wire,
    hub_node_id,
    looks_like_spec,
    media_payload,
    now_iso,
    payload_bytes,
    to_edge_event,
    to_legacy_dict,
)

__all__ = [
    "DEFAULT_NODE_ID", "DEFAULT_TRIGGER", "LEGACY_TO_METADATA", "LEGACY_TO_SPEC",
    "SPEC_MARKERS", "SPEC_TO_LEGACY", "command_from_legacy", "command_to_wire",
    "hub_node_id", "looks_like_spec", "media_payload", "now_iso", "payload_bytes",
    "to_edge_event", "to_legacy_dict",
]
