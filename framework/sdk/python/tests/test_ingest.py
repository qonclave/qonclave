"""
test_ingest.py — the two wire vocabularies map onto one model, losslessly.

The regression these guard against is subtle: a rename that silently drops a
field looks exactly like a working migration until someone notices the dashboard
lost a column, or a Policy stops seeing the threshold it branches on.
"""

from __future__ import annotations

import base64

import pytest

from qonclave.core.models import Command, EdgeEvent
from qonclave.hub import ingest as adapter

LEGACY = {
    "device_id": "unoq-01",
    "event_id": "unoq-01-abc123",
    "event_type": "person_detected",
    "edge_model": "video_object_detection",
    "edge_confidence": 0.87,
    "threshold": 0.5,
    "frame_id": "f-42",
    "created_at": "2026-08-05T12:00:00+00:00",
}


# --- legacy -> spec ---------------------------------------------------------

def test_every_legacy_field_lands_somewhere():
    e = adapter.to_edge_event(LEGACY)
    assert e.source_node_id == "unoq-01"
    assert e.event_id == "unoq-01-abc123"
    assert e.trigger == "person_detected"
    assert e.confidence == 0.87
    assert e.timestamp == "2026-08-05T12:00:00+00:00"
    # Demoted, not dropped.
    assert e.metadata["edge_model"] == "video_object_detection"
    assert e.metadata["threshold"] == 0.5
    assert e.metadata["frame_id"] == "f-42"


def test_round_trip_preserves_the_legacy_dict():
    """flat -> EdgeEvent -> flat must be the identity for known fields."""
    out = adapter.to_legacy_dict(adapter.to_edge_event(LEGACY))
    assert out == LEGACY


def test_unknown_fields_survive_the_round_trip():
    """A device may add a field before the hub learns about it."""
    raw = {**LEGACY, "battery_pct": 42, "firmware": "1.4.2"}
    e = adapter.to_edge_event(raw)
    assert e.metadata["battery_pct"] == 42
    assert adapter.to_legacy_dict(e)["firmware"] == "1.4.2"


def test_missing_required_fields_do_not_raise():
    """parse_edge_event has always been total; the schema must not change that."""
    e = adapter.to_edge_event({})
    assert e.source_node_id == adapter.DEFAULT_NODE_ID
    assert e.trigger == adapter.DEFAULT_TRIGGER
    assert e.event_id  # generated
    assert e.timestamp  # hub-stamped


def test_hub_stamps_time_when_the_device_sends_none():
    """Today's edge sends no time at all. The spec permits the hub to supply it,
    and for a device with no trustworthy clock this is the only real timestamp
    the event will ever carry."""
    raw = {k: v for k, v in LEGACY.items() if k != "created_at"}
    e = adapter.to_edge_event(raw)
    assert e.timestamp == e.hub_received_at


@pytest.mark.parametrize("bad", [87, -0.5, 1.5, "high", None])
def test_out_of_range_confidence_never_rejects_the_event(bad):
    """A device reporting 87 instead of 0.87 is a device bug. Losing the whole
    observation over it would be ours."""
    e = adapter.to_edge_event({**LEGACY, "edge_confidence": bad})
    assert e.confidence is None
    assert e.source_node_id == "unoq-01"  # the event still arrived
    if bad is not None:
        assert "invalid_confidence" in e.metadata


# --- spec -> spec -----------------------------------------------------------

SPEC = {
    "schema_version": "1.0",
    "event_id": "unoq-01-def456",
    "source_node_id": "unoq-01",
    "trigger": "person_detected",
    "confidence": 0.91,
    "timestamp": "2026-08-05T12:00:00+00:00",
    "metadata": {"edge_model": "video_object_detection"},
}


def test_spec_documents_pass_through_untranslated():
    e = adapter.to_edge_event(SPEC)
    assert e.source_node_id == "unoq-01"
    assert e.confidence == 0.91
    assert e.trigger == "person_detected"


def test_shape_detection_does_not_key_on_event_id():
    """event_id is the one name both vocabularies share, so it proves nothing."""
    assert adapter.looks_like_spec({"event_id": "x"}) is False
    assert adapter.looks_like_spec({"source_node_id": "x"}) is True
    assert adapter.looks_like_spec(LEGACY) is False
    assert adapter.looks_like_spec(SPEC) is True


def test_malformed_spec_document_degrades_rather_than_raising():
    e = adapter.to_edge_event({**SPEC, "relative_time": {"wake_counter": "not-an-int"}})
    assert e.event_id == "unoq-01-def456"
    assert e.metadata.get("malformed") is True


# --- payload ----------------------------------------------------------------

def test_base64_payload_round_trips():
    raw = b"\xff\xd8\xff\xe0 jpeg-ish bytes"
    e = EdgeEvent(event_id="e", source_node_id="n", trigger="t",
                  timestamp="2026-08-05T12:00:00Z",
                  payload=adapter.media_payload(raw))
    assert adapter.payload_bytes(e) == raw


def test_payload_absent_or_undecodable_returns_none():
    plain = EdgeEvent(event_id="e", source_node_id="n", trigger="t",
                      timestamp="2026-08-05T12:00:00Z")
    assert adapter.payload_bytes(plain) is None

    broken = EdgeEvent(event_id="e", source_node_id="n", trigger="t",
                       timestamp="2026-08-05T12:00:00Z",
                       payload={"media_type": "image/jpeg",
                                "data_encoding": "base64", "data": "!!!not base64!!!"})
    assert adapter.payload_bytes(broken) is None


# --- commands ---------------------------------------------------------------

def test_command_wire_form_satisfies_old_and_new_firmware_at_once():
    """One payload both firmwares can parse is what lets the device migrate
    without a synchronised deploy."""
    cmd = Command(command_id="c1", issuer_id="hub-01", target_id="unoq-01",
                  action="robot_move", parameters={"direction": "LEFT", "magnitude": 30})
    wire = adapter.command_to_wire(cmd)
    # what today's edge/main.py:658 dispatches on
    assert wire["type"] == "robot_move"
    assert wire["direction"] == "LEFT" and wire["magnitude"] == 30
    # what the spec says
    assert wire["action"] == "robot_move"
    assert wire["parameters"] == {"direction": "LEFT", "magnitude": 30}


def test_legacy_command_lifts_into_a_spec_command():
    cmd = adapter.command_from_legacy(
        {"type": "robot_move", "direction": "LEFT", "magnitude": 30},
        issuer_id="hub-01", target_id="unoq-01")
    assert cmd.action == "robot_move"
    assert cmd.parameters == {"direction": "LEFT", "magnitude": 30}
    assert cmd.target_id == "unoq-01"
    assert cmd.issued_at


def test_command_round_trip_is_stable():
    original = {"type": "robot_move", "direction": "RIGHT", "magnitude": 90}
    wire = adapter.command_to_wire(
        adapter.command_from_legacy(original, issuer_id="hub-01"))
    assert {k: wire[k] for k in original} == original
