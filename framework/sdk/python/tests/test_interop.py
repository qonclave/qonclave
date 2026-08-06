"""
test_interop.py — the C binding and the Python binding must agree on the wire.

Everything else in this suite checks Python against the spec. This checks Python against another
implementation, which is a different and stricter thing: two implementations can each satisfy the
schema and still be unable to talk to one another.

The bytes come from `qc_conformance`, which builds the documented fixture from a C struct and
encodes it canonically. If the C SDK has not been built, these skip rather than fail — the C build
needs a toolchain, and a missing artifact is not a Python bug.

Building the artifact:

    cmake -S framework/sdk/c -B build
    cmake --build build
    ctest --test-dir build -C Debug
"""

from __future__ import annotations

import json
import pathlib

import pytest

from qonclave.core.codec import decode_cbor, encode_cbor
from qonclave.core.models import CheckinRequest

FRAMEWORK = pathlib.Path(__file__).resolve().parents[3]
GENERATED = FRAMEWORK / "conformance" / "generated"
FIXTURE = FRAMEWORK / "conformance" / "cases" / "checkin" / "minimal-lora-sized" / "input.json"

C_UPLINK = GENERATED / "c-uplink-minimal.cbor"

needs_c = pytest.mark.skipif(
    not C_UPLINK.exists(),
    reason=f"{C_UPLINK.name} not built — run the C SDK's ctest first",
)


@needs_c
def test_python_decodes_c_uplink() -> None:
    """The direction that carries real traffic: device to hub.

    A disagreement here drops sensor data silently, which is the worst failure mode available —
    the device believes it reported and the hub never knew.
    """
    doc = decode_cbor(C_UPLINK.read_bytes(), CheckinRequest)

    assert doc.node_id == "s7"
    assert doc.wake_counter == 412
    assert len(doc.events) == 1

    event = doc.events[0]
    assert event.event_id == "e9"
    assert event.source_node_id == "s7"
    assert event.trigger == "threshold_crossed"

    # A duty-cycled device sends no absolute time. Python must accept that rather than requiring
    # a timestamp it cannot know.
    assert event.timestamp is None
    assert event.relative_time is not None
    assert event.relative_time.wake_counter == 412
    assert event.relative_time.ms_since_wake == 180

    assert doc.power is not None
    assert doc.power.battery_pct == pytest.approx(62.0)
    assert doc.power.on_mains is False


@needs_c
def test_c_uplink_matches_the_json_fixture() -> None:
    """C's encoding and the JSON fixture describe the same observation.

    Compared field by field rather than by dict equality: the C struct types the metadata value as
    a string where the JSON fixture has a number, which the schema permits and which does not
    affect what either side does with it.
    """
    from_c = decode_cbor(C_UPLINK.read_bytes(), CheckinRequest)
    from_json = CheckinRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))

    assert from_c.node_id == from_json.node_id
    assert from_c.wake_counter == from_json.wake_counter
    assert len(from_c.events) == len(from_json.events)

    c_ev, j_ev = from_c.events[0], from_json.events[0]
    assert (c_ev.event_id, c_ev.source_node_id, c_ev.trigger) == (
        j_ev.event_id, j_ev.source_node_id, j_ev.trigger
    )
    assert c_ev.relative_time.model_dump() == j_ev.relative_time.model_dump()
    assert set(c_ev.metadata) == set(j_ev.metadata)
    assert str(c_ev.metadata["m"]) == str(j_ev.metadata["m"])


@needs_c
def test_c_uplink_fits_a_lora_frame() -> None:
    """The number spec/v1/profiles/minimal.md commits to, measured on C's actual output rather
    than on Python's."""
    size = C_UPLINK.stat().st_size
    assert size <= 242, f"C uplink is {size}B, over the 242B LoRaWAN ceiling"


@needs_c
def test_the_size_gap_between_bindings_is_fully_accounted_for() -> None:
    """The two encodings differ by a known amount, for known reasons, and this pins both.

    A tolerance band would have been easier and is what this test used to do. It is the wrong
    shape: a band wide enough to cover the legitimate difference is also wide enough to hide a
    field one binding emits and the other drops, which is exactly the class of bug the C/Python
    cross-check exists to catch. So the gap is enumerated instead — if it moves, something
    changed, and the test says which direction.
    """
    c_size = C_UPLINK.stat().st_size
    py = CheckinRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    py_size = len(encode_cbor(py, int_keys=True))

    # C emits with omit_identity, permitted on `minimal` only: schema_version leaves the check-in
    # and the event nested inside it, 5 bytes each with the key map already applied.
    schema_version_omitted = 10

    # C types the moisture reading as text ("11"), Python as an integer. Both are schema-valid;
    # a 2-byte text string costs 2 more than a small integer.
    metadata_as_text = 2

    expected = py_size - schema_version_omitted + metadata_as_text
    assert c_size == expected, (
        f"C encodes to {c_size}B, Python to {py_size}B. Accounting for the {schema_version_omitted}B "
        f"identity omission and the {metadata_as_text}B metadata typing predicts {expected}B. "
        f"An unexplained difference means one binding is emitting a field the other is not."
    )
