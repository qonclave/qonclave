"""
adapter.py — translation between the pre-spec demo vocabulary and spec/v1.

This is the ONLY place either vocabulary is written down. Everything upstream of
it speaks `qonclave.core.models`; everything downstream of the wire may speak
whichever an edge device happens to have been flashed with.

The two disagree in almost every field name:

    device_id        -> source_node_id      edge_model  -> metadata.edge_model
    event_type       -> trigger             threshold   -> metadata.threshold
    edge_confidence  -> confidence          frame_id    -> metadata.frame_id
    created_at       -> timestamp           event_id    -> event_id  (the only match)

Two rules govern everything here:

* **Nothing raises.** `parse_edge_event` has always been tolerant of whatever a
  constrained device sends, and CONVENTIONS.md section 4 makes that a rule
  rather than an accident: a device sending garbage should be rejected, not able
  to take the hub down. Malformed values are preserved in `metadata` instead of
  failing validation.
* **Nothing is dropped.** Unrecognised keys land in `metadata`, so a field this
  hub does not understand still reaches the Policy and still round-trips back
  out. A document must not lose data merely by transiting a hop.

Origin: the field map in docs/plan_hub_edge_convergence.md section 1.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import logging
import uuid
from typing import Any

from qonclave.core.models import Command, EdgeEvent, MediaPayload

log = logging.getLogger("qonclave.hub")

# Legacy names, in the order transport.EDGE_EVENT_FIELDS declares them.
LEGACY_TO_SPEC = {
    "device_id": "source_node_id",
    "event_type": "trigger",
    "edge_confidence": "confidence",
    "created_at": "timestamp",
    "event_id": "event_id",
}

# Legacy names that become metadata rather than top-level spec fields. They are
# real information, just not part of the normative envelope.
LEGACY_TO_METADATA = ("edge_model", "threshold", "frame_id")

SPEC_TO_LEGACY = {v: k for k, v in LEGACY_TO_SPEC.items()}

# A document carrying any of these is already spec-shaped and needs no
# translation. `event_id` is deliberately excluded — it is the one name both
# vocabularies share, so its presence proves nothing.
SPEC_MARKERS = ("source_node_id", "trigger", "schema_version", "relative_time")

DEFAULT_TRIGGER = "unspecified"
DEFAULT_NODE_ID = "unknown"


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def looks_like_spec(raw: dict[str, Any]) -> bool:
    """Whether this document is already in spec/v1 shape."""
    return any(k in raw for k in SPEC_MARKERS)


def _coerce_confidence(value: Any, metadata: dict[str, Any]) -> float | None:
    """Parse a confidence, or park it in metadata and return None.

    The schema constrains confidence to 0..1. A device reporting 87 instead of
    0.87 is a real bug, but it is the device's bug — rejecting the whole event
    would lose an observation that a Policy might still act on, so the odd value
    is preserved under `metadata.invalid_confidence` where it is visible without
    being load-bearing.
    """
    if value is None:
        return None
    try:
        c = float(value)
    except (TypeError, ValueError):
        metadata["invalid_confidence"] = value
        return None
    if not 0.0 <= c <= 1.0:
        log.warning("edge confidence %r outside [0,1]; keeping it in metadata", value)
        metadata["invalid_confidence"] = c
        return None
    return c


def to_edge_event(raw: dict[str, Any], *, stamp_time: bool = True) -> EdgeEvent:
    """Build a validated EdgeEvent from either vocabulary.

    `stamp_time` fills in `timestamp` when the device sent none. Today's edge
    sends no time at all and the hub records receipt itself; the spec permits
    exactly this, and for a device with no trustworthy clock the hub's stamp is
    the only authoritative time the event will ever have.
    """
    if looks_like_spec(raw):
        return _from_spec(raw, stamp_time=stamp_time)
    return _from_legacy(raw, stamp_time=stamp_time)


def _from_spec(raw: dict[str, Any], *, stamp_time: bool) -> EdgeEvent:
    data = dict(raw)
    data.setdefault("event_id", _new_event_id(data.get("source_node_id")))
    data.setdefault("source_node_id", DEFAULT_NODE_ID)
    data.setdefault("trigger", DEFAULT_TRIGGER)
    data["hub_received_at"] = now_iso()
    if stamp_time and not data.get("timestamp") and not data.get("relative_time"):
        data["timestamp"] = data["hub_received_at"]

    metadata = dict(data.get("metadata") or {})
    data["confidence"] = _coerce_confidence(data.get("confidence"), metadata)
    data["metadata"] = metadata

    try:
        return EdgeEvent.model_validate(data)
    except Exception as e:
        # Never 500 on a malformed document. Keep the identity fields, demote
        # everything else to metadata, and let the Policy decide what to do with
        # an event it can see but not fully trust.
        log.warning("spec-shaped event failed validation (%s); degrading it", e)
        return EdgeEvent(
            event_id=str(data.get("event_id")),
            source_node_id=str(data.get("source_node_id") or DEFAULT_NODE_ID),
            trigger=str(data.get("trigger") or DEFAULT_TRIGGER),
            timestamp=data.get("hub_received_at"),
            hub_received_at=data.get("hub_received_at"),
            metadata={"malformed": True, "raw": _stringify(raw)},
        )


def _from_legacy(raw: dict[str, Any], *, stamp_time: bool) -> EdgeEvent:
    metadata: dict[str, Any] = {}
    for key in LEGACY_TO_METADATA:
        if raw.get(key) is not None:
            metadata[key] = raw[key]

    # Anything we do not recognise is still information. Carrying it through
    # means a device can add a field without waiting for the hub to learn it.
    known = set(LEGACY_TO_SPEC) | set(LEGACY_TO_METADATA)
    for key, value in raw.items():
        if key not in known and value is not None:
            metadata[key] = value

    received = now_iso()
    timestamp = raw.get("created_at")
    if not timestamp and stamp_time:
        timestamp = received

    return EdgeEvent(
        event_id=str(raw.get("event_id") or _new_event_id(raw.get("device_id"))),
        source_node_id=str(raw.get("device_id") or DEFAULT_NODE_ID),
        trigger=str(raw.get("event_type") or DEFAULT_TRIGGER),
        timestamp=timestamp,
        hub_received_at=received,
        confidence=_coerce_confidence(raw.get("edge_confidence"), metadata),
        metadata=metadata,
    )


def to_legacy_dict(event: EdgeEvent) -> dict[str, Any]:
    """Render an EdgeEvent in the vocabulary a pre-spec Policy expects.

    Used only while `hub.Policy` still takes a dict. Phase 3 retires it.
    """
    metadata = dict(event.metadata or {})
    out: dict[str, Any] = {
        "device_id": event.source_node_id,
        "event_id": event.event_id,
        "event_type": event.trigger,
        "edge_confidence": event.confidence,
        "created_at": event.timestamp,
    }
    for key in LEGACY_TO_METADATA:
        if key in metadata:
            out[key] = metadata.pop(key)
    # Whatever is left was never part of the legacy vocabulary; pass it through
    # flat rather than silently dropping it.
    out.update(metadata)
    return {k: v for k, v in out.items() if v is not None}


def payload_bytes(event: EdgeEvent) -> bytes | None:
    """Decode an inline payload, or None if the event carries no usable one.

    Per decision D3 the frame travels base64 inside the document. A frame that
    arrived as the raw HTTP body instead is already on disk and never reaches
    here.
    """
    payload = event.payload
    if payload is None or not payload.data:
        return None
    encoding = getattr(payload.data_encoding, "value", payload.data_encoding)
    if encoding != "base64":
        log.warning("payload data_encoding=%r is not base64; ignoring it", encoding)
        return None
    try:
        return base64.b64decode(payload.data, validate=True)
    except (binascii.Error, ValueError) as e:
        log.warning("payload base64 decode failed: %s", e)
        return None


def media_payload(data: bytes, media_type: str = "image/jpeg") -> MediaPayload:
    """Wrap raw bytes as a spec MediaPayload. Used by tests and by the edge."""
    return MediaPayload(
        media_type=media_type,
        data_encoding="base64",
        data=base64.b64encode(data).decode("ascii"),
    )


# --- commands ---------------------------------------------------------------

def command_to_wire(command: Command) -> dict[str, Any]:
    """Flatten a spec Command into the shape today's edge firmware parses.

    edge/.../main.py dispatches on a top-level `type` and reads `direction` and
    `magnitude` beside it; the spec nests those under `action` and `parameters`.
    Both are emitted, so one payload satisfies old and new firmware at once and
    the device can migrate without a synchronised deploy.
    """
    wire = command.model_dump(mode="json", exclude_none=True)
    wire["type"] = command.action
    wire.update(command.parameters or {})
    return wire


def command_from_legacy(raw: dict[str, Any], *, issuer_id: str,
                        target_id: str | None = None) -> Command:
    """Lift a legacy command dict into a spec Command."""
    params = {k: v for k, v in raw.items() if k != "type"}
    return Command(
        command_id=f"cmd-{uuid.uuid4().hex[:12]}",
        issuer_id=issuer_id,
        target_id=target_id,
        action=str(raw.get("type") or "unspecified"),
        parameters=params,
        issued_at=now_iso(),
    )


# --- internals --------------------------------------------------------------

def _new_event_id(node_id: Any) -> str:
    prefix = str(node_id) if node_id else "evt"
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _stringify(raw: dict[str, Any]) -> str:
    """Best-effort rendering of a document we could not validate."""
    try:
        return repr(raw)[:500]
    except Exception:
        return "<unrenderable>"
