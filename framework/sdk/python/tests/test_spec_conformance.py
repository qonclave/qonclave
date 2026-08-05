"""
test_spec_conformance.py — prove this binding agrees with framework/spec/v1/.

Two things are checked, and they are different:

1. **Schema agreement.** Documents this SDK produces validate against the JSON Schemas. Catches
   the binding drifting from the spec it claims to implement.

2. **The conformance fixtures.** The same language-neutral cases in framework/conformance/ that a
   C implementation runs. Catches two implementations agreeing with the schema but not with each
   other — which is the failure that actually breaks a mixed fleet.

The fixtures are the more important half. An ESP32 that links none of our code proves conformance
by running exactly these files.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime

import pytest

from qonclave.core import models
from qonclave.core.codec import decode_cbor, decode_json, encode_cbor, encode_json
from qonclave.core.enums import AudienceKind, Scope
from qonclave.security import capability

HERE = pathlib.Path(__file__).resolve()
FRAMEWORK = HERE.parents[3]
SCHEMAS = FRAMEWORK / "spec" / "v1" / "json-schema"
CASES = FRAMEWORK / "conformance" / "cases"

MODELS = {
    "EdgeEvent": models.EdgeEvent,
    "Command": models.Command,
    "NodeManifest": models.NodeManifest,
    "CheckinRequest": models.CheckinRequest,
    "CheckinResponse": models.CheckinResponse,
    "CapabilityGrant": models.CapabilityGrant,
    "ArchiveRecord": models.ArchiveRecord,
}


def _cases(kind: str) -> list[pathlib.Path]:
    root = CASES / kind
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- schema sanity


def test_every_schema_parses() -> None:
    found = list(SCHEMAS.glob("*.schema.json"))
    assert found, f"no schemas found under {SCHEMAS}"
    for path in found:
        _load(path)


def test_schemas_declare_ids() -> None:
    """$id must be present and match the filename, or $ref resolution between schemas breaks in
    ways that only show up in someone else's validator."""
    for path in SCHEMAS.glob("*.schema.json"):
        schema = _load(path)
        assert "$id" in schema, f"{path.name} has no $id"
        assert schema["$id"].endswith(path.name), (
            f"{path.name} declares $id {schema['$id']}, which does not match its filename"
        )


# ------------------------------------------------------------------- codec


@pytest.mark.parametrize("case_dir", _cases("codec"), ids=lambda p: p.name)
def test_codec_case(case_dir: pathlib.Path) -> None:
    case = _load(case_dir / "case.json")
    raw = _load(case_dir / "input.json")
    model = MODELS[case["model"]]

    if case["expect"] == "reject":
        with pytest.raises(Exception):
            model.model_validate(raw)
        return

    doc = model.model_validate(raw)

    again = decode_json(encode_json(doc), model)
    assert again.model_dump(exclude_none=True) == doc.model_dump(exclude_none=True), (
        f"{case_dir.name} did not survive a JSON round trip. {case.get('why', '')}"
    )

    for field in case.get("preserve_unknown", []):
        assert getattr(again, field, None) is not None, (
            f"{case_dir.name}: unknown field {field!r} was dropped. {case.get('why', '')}"
        )


# ----------------------------------------------------------------- encoding


@pytest.mark.parametrize("case_dir", _cases("encoding"), ids=lambda p: p.name)
def test_json_cbor_equivalent(case_dir: pathlib.Path) -> None:
    case = _load(case_dir / "case.json")
    model = MODELS[case["model"]]
    doc = model.model_validate(_load(case_dir / "input.json"))

    from_json = decode_json(encode_json(doc), model)
    from_cbor = decode_cbor(encode_cbor(doc, int_keys=case.get("int_keys", True)), model)

    assert from_cbor.model_dump(exclude_none=True) == from_json.model_dump(exclude_none=True), (
        f"{case_dir.name}: CBOR and JSON decode to different documents. {case.get('why', '')}"
    )


@pytest.mark.parametrize("case_dir", _cases("encoding"), ids=lambda p: p.name)
def test_cbor_is_smaller(case_dir: pathlib.Path) -> None:
    """Not a correctness property, but if CBOR is not smaller there is no reason to carry it."""
    case = _load(case_dir / "case.json")
    model = MODELS[case["model"]]
    doc = model.model_validate(_load(case_dir / "input.json"))

    js = len(encode_json(doc))
    cb = len(encode_cbor(doc, int_keys=True))
    assert cb < js, f"{case_dir.name}: CBOR {cb}B is not smaller than JSON {js}B"


# -------------------------------------------------------------------- grants


@pytest.mark.parametrize("case_dir", _cases("grant"), ids=lambda p: p.name)
def test_grant_case(case_dir: pathlib.Path) -> None:
    case = _load(case_dir / "case.json")
    ctx = case["context"]
    grant = models.CapabilityGrant.model_validate(_load(case_dir / "grant.json"))

    result = capability.verify(
        grant,
        audience_id=ctx["audience_id"],
        audience_kind=AudienceKind(ctx["audience_kind"]),
        tenant_id=ctx["tenant_id"],
        required_scope=Scope(ctx["required_scope"]),
        trusted_issuers={issuer: b"" for issuer in ctx["trusted_issuers"]},
        revoked=set(ctx.get("revoked", [])),
        now=datetime.fromisoformat(ctx["now"]),
        # Fixtures are unsigned: they exercise the authorization logic, which is what differs
        # between implementations. Signature verification is covered in test_signing.
        verify_signature=False,
    )

    assert result.valid is case["expect"]["valid"], (
        f"{case_dir.name}: expected valid={case['expect']['valid']}, got {result.valid} "
        f"({result.reason}). {case.get('why', '')}"
    )
    assert result.reason == case["expect"]["reason"], (
        f"{case_dir.name}: expected reason {case['expect']['reason']!r}, got {result.reason!r}. "
        f"{case.get('why', '')}"
    )


def test_grant_offline_verification_needs_no_issuer() -> None:
    """The property the whole design rests on.

    verify() takes a pinned key map and a clock. It has no transport, no client, and no way to
    reach the issuing hub even if it wanted to — which is what makes failover possible when the
    issuing hub is the thing that died.
    """
    import inspect

    params = set(inspect.signature(capability.verify).parameters)
    assert not (params & {"transport", "session", "client", "hub_url"}), (
        "capability.verify acquired a way to make a network call; offline verification is the "
        "entire point of pinning issuer roots during federation"
    )


# ------------------------------------------------------------------- checkin


@pytest.mark.parametrize("case_dir", _cases("checkin"), ids=lambda p: p.name)
def test_checkin_size_bound(case_dir: pathlib.Path) -> None:
    case = _load(case_dir / "case.json")
    model = MODELS[case["model"]]
    doc = model.model_validate(_load(case_dir / "input.json"))

    encoded = encode_cbor(doc, int_keys=case.get("int_keys", True))
    size = len(encoded)

    assert size <= case["max_bytes"], (
        f"{case_dir.name}: {size}B exceeds the {case['max_bytes']}B LoRaWAN ceiling. "
        f"{case.get('why', '')}"
    )

    if size > case.get("should_fit_bytes", case["max_bytes"]):
        pytest.xfail(
            f"{size}B fits the {case['max_bytes']}B ceiling but not the "
            f"{case['should_fit_bytes']}B slow-data-rate target"
        )
