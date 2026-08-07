# SPDX-License-Identifier: MPL-2.0

"""
test_hub_protocol.py — this device's wire contract, checked against the spec.

hub_protocol.py is hand-written against spec/v1 rather than importing the
qonclave SDK, because carrying a Python dependency onto flashed firmware costs
more than the protocol does. The risk that buys is drift, so the last test here
validates a real emitted event against the actual schema file — if the two ever
disagree, this fails rather than the device silently going quiet in the field.
"""

import base64
import json
import os
import sys
from datetime import datetime, timedelta, UTC

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from hub_protocol import (  # noqa: E402
    build_edge_event, command_expired, normalize_command,
)

FRAME = b"\xff\xd8\xff\xe0 pretend jpeg"


# --- outbound events --------------------------------------------------------

def test_event_uses_spec_field_names():
    e = build_edge_event(node_id="unoq-01", trigger="person_detected", confidence=0.87)
    assert e["source_node_id"] == "unoq-01"
    assert e["trigger"] == "person_detected"
    assert e["confidence"] == 0.87
    assert e["schema_version"] == "1.0"
    # The pre-spec names must be gone, not merely supplemented.
    for legacy in ("device_id", "event_type", "edge_confidence", "created_at"):
        assert legacy not in e


def test_frame_is_base64_in_the_payload():
    e = build_edge_event(node_id="n", trigger="t", frame=FRAME)
    assert e["payload"]["media_type"] == "image/jpeg"
    assert e["payload"]["data_encoding"] == "base64"
    assert base64.b64decode(e["payload"]["data"]) == FRAME


def test_event_without_a_frame_omits_payload_entirely():
    """A payload-free event is legal: a threshold crossing has nothing to look
    at. An empty payload object would not be — the schema wants it absent."""
    e = build_edge_event(node_id="n", trigger="t")
    assert "payload" not in e


def test_edge_model_and_threshold_travel_as_metadata():
    e = build_edge_event(node_id="n", trigger="t",
                         metadata={"edge_model": "video_object_detection",
                                   "threshold": 0.7})
    assert e["metadata"]["edge_model"] == "video_object_detection"
    assert e["metadata"]["threshold"] == 0.7


def test_event_ids_are_unique_per_call():
    ids = {build_edge_event(node_id="n", trigger="t")["event_id"] for _ in range(50)}
    assert len(ids) == 50


# --- inbound commands -------------------------------------------------------

def test_spec_command_shape_is_flattened():
    cmd = normalize_command({
        "schema_version": "1.0", "command_id": "c1", "issuer_id": "hub-01",
        "action": "robot_move", "parameters": {"direction": "LEFT", "magnitude": 30},
    })
    assert cmd["action"] == "robot_move"
    assert cmd["direction"] == "LEFT"
    assert cmd["magnitude"] == 30


def test_pre_spec_command_shape_still_works():
    """The device must keep working against a hub that has not been updated."""
    cmd = normalize_command({"type": "robot_move", "direction": "LEFT", "magnitude": 30})
    assert cmd["action"] == "robot_move"
    assert cmd["direction"] == "LEFT"
    assert cmd["magnitude"] == 30


def test_both_shapes_in_one_payload_agree():
    """What the hub actually publishes during the migration: one payload
    carrying both vocabularies. Neither reading may win differently."""
    both = {"type": "robot_move", "direction": "LEFT", "magnitude": 30,
            "action": "robot_move", "parameters": {"direction": "LEFT", "magnitude": 30}}
    cmd = normalize_command(both)
    assert cmd["action"] == "robot_move"
    assert cmd["direction"] == "LEFT" and cmd["magnitude"] == 30


@pytest.mark.parametrize("junk", [None, "string", 42, [], {}, {"parameters": {}}])
def test_unusable_commands_return_none(junk):
    assert normalize_command(junk) is None


# --- expiry -----------------------------------------------------------------

def test_expired_command_is_dropped():
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert command_expired({"action": "robot_move", "expires_at": past}) is True


def test_live_command_is_not_dropped():
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    assert command_expired({"action": "robot_move", "expires_at": future}) is False


def test_command_without_expiry_never_expires():
    assert command_expired({"action": "robot_move"}) is False


def test_unparseable_expiry_is_treated_as_live():
    """One malformed field from the hub must not be able to disable the device.
    Acting on a stale command is the lesser failure."""
    assert command_expired({"action": "robot_move", "expires_at": "not-a-date"}) is False


def test_naive_expiry_is_read_as_utc():
    past = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None).isoformat()
    assert command_expired({"action": "robot_move", "expires_at": past}) is True


# --- drift guard ------------------------------------------------------------

def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(here, "framework", "spec")):
            return here
        here = os.path.dirname(here)
    return None


def test_emitted_event_validates_against_the_real_schema():
    """The whole reason this module is hand-written is that the device should
    not carry the SDK. This is the check that keeps that honest."""
    jsonschema = pytest.importorskip("jsonschema")
    root = _repo_root()
    if root is None:
        pytest.skip("repo root not found; running outside a checkout")

    referencing = pytest.importorskip("referencing")
    from referencing.jsonschema import DRAFT202012

    schema_dir = os.path.join(root, "framework", "spec", "v1", "json-schema")

    # The schemas declare absolute $ids (https://qonclave.dev/spec/v1/...), so a
    # naive resolver tries to fetch them over the network. Registering every
    # local file under its own $id resolves $refs offline — which is also the
    # only way this test can run on a device with no route to the internet.
    resources = []
    for name in os.listdir(schema_dir):
        if not name.endswith(".schema.json"):
            continue
        with open(os.path.join(schema_dir, name), encoding="utf-8") as f:
            doc = json.load(f)
        resources.append((doc["$id"],
                          referencing.Resource.from_contents(doc, default_specification=DRAFT202012)))
    registry = referencing.Registry().with_resources(resources)

    with open(os.path.join(schema_dir, "edge-event.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)

    event = build_edge_event(
        node_id="unoq-01", trigger="person_detected", confidence=0.87, frame=FRAME,
        metadata={"edge_model": "video_object_detection", "threshold": 0.7},
    )

    jsonschema.Draft202012Validator(schema, registry=registry).validate(event)


def test_payload_free_event_also_validates():
    """The minimal-profile shape: an observation with nothing to look at."""
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    from referencing.jsonschema import DRAFT202012

    root = _repo_root()
    if root is None:
        pytest.skip("repo root not found; running outside a checkout")
    schema_dir = os.path.join(root, "framework", "spec", "v1", "json-schema")

    resources = []
    for name in os.listdir(schema_dir):
        if name.endswith(".schema.json"):
            with open(os.path.join(schema_dir, name), encoding="utf-8") as f:
                doc = json.load(f)
            resources.append((doc["$id"],
                              referencing.Resource.from_contents(doc, default_specification=DRAFT202012)))
    registry = referencing.Registry().with_resources(resources)

    with open(os.path.join(schema_dir, "edge-event.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)

    event = build_edge_event(node_id="sensor-01", trigger="threshold_crossed")
    jsonschema.Draft202012Validator(schema, registry=registry).validate(event)
