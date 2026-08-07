"""
test_schema_validation.py — documents this SDK produces validate against spec/v1.

This closes a real gap. `test_spec_conformance.py` claims to check that "documents this SDK
produces validate against the JSON Schemas", but it validates them against the *pydantic models* —
which are the binding, not the definition. When the two disagreed, the models won silently.

They did disagree. `common.schema.json` defines `complexity` and `urgency` as strings
("vlm_reason", "high"), the models serialized them as the raw IntEnum values (4, 2), and the
conformance fixture agreed with the models. Every task descriptor this SDK emitted was invalid
against its own spec, and nothing failed.

CONVENTIONS.md rule 1 is the arbiter: the spec is normative, so the fix went in the SDK. This file
is what stops it recurring — it runs the actual JSON Schemas over actual serialized documents.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from qonclave.core import models

jsonschema = pytest.importorskip("jsonschema")
referencing = pytest.importorskip("referencing")
from referencing.jsonschema import DRAFT202012  # noqa: E402

FRAMEWORK = pathlib.Path(__file__).resolve().parents[3]
SCHEMAS = FRAMEWORK / "spec" / "v1" / "json-schema"
CASES = FRAMEWORK / "conformance" / "cases"


def _registry() -> "referencing.Registry":
    """Resolve $refs from local files.

    The schemas declare absolute $ids (https://qonclave.dev/spec/v1/...), so a default resolver
    tries to fetch them. Registering each local file under its own $id keeps this offline, which
    it must be — CI has no business reaching the network to validate a local schema.
    """
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        resources.append((doc["$id"], referencing.Resource.from_contents(
            doc, default_specification=DRAFT202012)))
    return referencing.Registry().with_resources(resources)


def _validator(schema_name: str):
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, registry=_registry())


DOCUMENTS = {
    "edge-event.schema.json": lambda: models.EdgeEvent(
        event_id="evt-1", source_node_id="unoq-01", trigger="person_detected",
        timestamp="2026-08-05T12:00:00+00:00", confidence=0.87,
        metadata={"edge_model": "video_object_detection"},
        task=models.TaskDescriptor(
            complexity=models.Complexity.VLM_REASON,
            urgency=models.Urgency.HIGH,
            deadline_ms=3000, remaining_ms=2800, hops=["edge"],
        ),
    ),
    "command.schema.json": lambda: models.Command(
        command_id="cmd-1", issuer_id="hub-01", target_id="unoq-01",
        action="robot_move", parameters={"direction": "LEFT", "magnitude": 30},
        issued_at="2026-08-05T12:00:00+00:00",
    ),
    "node-manifest.schema.json": lambda: models.NodeManifest(
        node_id="hub-01", node_type="hub",
        capabilities=models.Capabilities(max_complexity=models.Complexity.VLM_REASON),
    ),
}


@pytest.mark.parametrize("schema_name", sorted(DOCUMENTS))
def test_serialized_documents_validate_against_their_schema(schema_name: str) -> None:
    doc = DOCUMENTS[schema_name]()
    _validator(schema_name).validate(doc.model_dump(mode="json", exclude_none=True))


def test_task_descriptor_enums_serialize_as_strings() -> None:
    """The specific bug. `complexity: 4` is what the models used to emit and what the schema
    rejects."""
    dumped = models.TaskDescriptor(
        complexity=models.Complexity.VLM_REASON, urgency=models.Urgency.HIGH,
    ).model_dump(mode="json")
    assert dumped["complexity"] == "vlm_reason"
    assert dumped["urgency"] == "high"


def test_int_forms_are_still_accepted_on_input() -> None:
    """Forward compatibility: a peer emitting the old int form stays readable. Same reasoning as
    extra='allow' — a document must not become unparseable because a hop is older."""
    d = models.TaskDescriptor.model_validate({"complexity": 4, "urgency": 2})
    assert d.complexity is models.Complexity.VLM_REASON
    assert d.urgency is models.Urgency.HIGH


def test_ordering_survives_the_string_wire_form() -> None:
    """The reason these are IntEnums at all: placement prunes with `max_complexity >= complexity`,
    so the ordering must remain usable after a round trip through the wire form."""
    d = models.TaskDescriptor.model_validate({"complexity": "vlm_reason"})
    assert d.complexity >= models.Complexity.CLASSIFY
    assert not d.complexity >= models.Complexity.LLM_REASON


@pytest.mark.parametrize("case_dir", sorted(
    (p for p in (CASES / "codec").iterdir() if p.is_dir()),
    key=lambda p: p.name,
) if (CASES / "codec").is_dir() else [])
def test_conformance_fixtures_are_valid_against_the_schemas(case_dir: pathlib.Path) -> None:
    """The fixtures are what other language bindings are checked against. One that contradicts the
    schema teaches every implementation the same wrong thing."""
    meta_path = case_dir / "case.json"
    raw = json.loads((case_dir / "input.json").read_text(encoding="utf-8"))

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    schema_name = None
    if meta:
        model_name = meta.get("model")
        schema_name = {
            "EdgeEvent": "edge-event.schema.json",
            "Command": "command.schema.json",
            "NodeManifest": "node-manifest.schema.json",
            "CheckinRequest": "checkin.schema.json",
            "CheckinResponse": "checkin.schema.json",
            "CapabilityGrant": "capability-grant.schema.json",
            "ArchiveRecord": "archive-record.schema.json",
        }.get(model_name)
    if schema_name is None:
        pytest.skip(f"{case_dir.name}: no model declared, cannot pick a schema")
    if schema_name == "checkin.schema.json":
        pytest.skip("check-in request/response live under $defs; needs a sub-schema selector")

    validator = _validator(schema_name)

    if meta.get("expect") == "reject":
        # A fixture the SDK must reject should be rejected by the schema for the SAME reason.
        # If the schema accepts it, the rejection is a private rule of this binding rather than a
        # property of the protocol, and another implementation would legitimately disagree.
        errors = list(validator.iter_errors(raw))
        assert errors, (
            f"{case_dir.name} expects rejection ({meta.get('reason')}) but the schema accepts it; "
            "the rule lives only in the Python binding"
        )
        return

    validator.validate(raw)
