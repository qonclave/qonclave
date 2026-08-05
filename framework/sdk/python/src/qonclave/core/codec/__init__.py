"""
codec — one model tree, two wire encodings.

JSON is the default. CBOR exists because a LoRaWAN payload is 51-242 bytes and a JSON check-in
with string keys and an ISO-8601 timestamp does not fit in one.

Note where the saving actually is. On the reference fixture, 285 bytes of JSON become 223 as CBOR
with string keys and 71 with the integer key map: CBOR alone takes off ~22%, and the key map takes
~68% of what is left. Adopting CBOR and stopping there gets roughly a fifth of the available
benefit and still does not fit a slow-rate frame.

`decode(encode(doc)) == doc` must hold for both, and JSON/CBOR equivalence is asserted by
conformance/cases/encoding/. That equivalence is the whole reason a C sensor and a Python hub can
disagree about serialization without disagreeing about meaning.

Spec: spec/v1/encodings/
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

# Integer key maps from spec/v1/encodings/cbor.md. APPEND-ONLY and frozen for v1: reusing a number
# for a different field would silently change the meaning of documents from devices still in the
# field, which cannot be updated.
CBOR_KEYS: dict[str, dict[str, int]] = {
    "CheckinRequest": {
        "schema_version": 1, "node_id": 2, "tenant_id": 3, "wake_counter": 4,
        "events": 5, "power": 6, "config_version": 7, "ack": 8, "grant": 9, "signature": 10,
    },
    "CheckinResponse": {
        "schema_version": 1, "server_time": 2, "accepted": 3, "commands": 4,
        "config": 5, "next_checkin_s": 6, "signature": 7,
    },
    "EdgeEvent": {
        "schema_version": 1, "event_id": 2, "source_node_id": 3, "tenant_id": 4,
        "timestamp": 5, "relative_time": 6, "trigger": 7, "confidence": 8,
        "payload": 9, "task": 10, "metadata": 11, "power": 12, "signature": 13,
        "hub_received_at": 14,
    },
    "Command": {
        "schema_version": 1, "command_id": 2, "issuer_id": 3, "target_id": 4,
        "tenant_id": 5, "action": 6, "parameters": 7, "issued_at": 8,
        "expires_at": 9, "signature": 10,
    },
    "Power": {"battery_pct": 1, "on_mains": 2, "thermal_headroom_c": 3, "duty_cycle_s": 4},
    "RelativeTime": {"wake_counter": 1, "ms_since_wake": 2, "uncertainty_s": 3},
    "TaskDescriptor": {
        "complexity": 1, "urgency": 2, "privacy": 3, "use_case": 4,
        "deadline_ms": 5, "remaining_ms": 6, "hops": 7,
    },
    "Signature": {"alg": 1, "key_id": 2, "value": 3},
}

_NESTED = {
    "power": "Power",
    "relative_time": "RelativeTime",
    "task": "TaskDescriptor",
    "signature": "Signature",
}

# Fields holding a LIST of documents, each of which has its own key map. Missing these was a real
# bug: the top-level keys mapped to integers while the events nested inside stayed as strings,
# costing ~87 bytes on a message that has to fit in 242. It survived because JSON and CBOR still
# decoded to the same document, so the equivalence test passed — only comparing sizes against the
# C encoder exposed it.
_NESTED_LIST = {
    "events": "EdgeEvent",
    "commands": "Command",
}


class CodecError(ValueError):
    """Encoding or decoding failed."""


def _dump(model: BaseModel) -> dict[str, Any]:
    """Dump a model, dropping fields that carry no information.

    `exclude_none` handles absent optionals. Empty collections need dropping too, and pydantic has
    no flag for it: every list/dict field in these schemas is optional with an empty default, so a
    receiver reconstructs `[]` whether or not we sent it. Left in, they are pure overhead — a
    check-in with nothing to acknowledge was spending two bytes on `ack: []`, on the one profile
    where the whole point is that bytes are scarce. The C binding never emitted them, which is why
    the two bindings disagreed on size for the same document.

    Only *empty* collections go. A populated one is information; `0`, `false`, and `""` are values
    a device meant to send and are kept.
    """

    def is_empty(value: Any) -> bool:
        # Tested by type rather than truthiness: `0`, `False`, and `""` are all falsy and all
        # meaningful. Only an empty container is nothing.
        return isinstance(value, (list, dict)) and not value

    def prune(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: prune(v) for k, v in value.items() if not is_empty(v)}
        if isinstance(value, list):
            return [prune(v) for v in value]
        return value

    return prune(model.model_dump(mode="json", exclude_none=True))


# ----------------------------------------------------------------------- JSON


def encode_json(model: BaseModel) -> bytes:
    """Serialize to JSON bytes, omitting None-valued optional fields and empty collections.

    Omission rather than explicit null matters on constrained links, and both forms decode
    identically, so there is no reason to spend the bytes.
    """
    return json.dumps(
        _dump(model),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def decode_json(raw: bytes, model: type[M]) -> M:
    try:
        return model.model_validate(json.loads(raw.decode("utf-8")))
    except Exception as exc:
        raise CodecError(f"cannot decode {model.__name__} from JSON: {exc}") from exc


# ----------------------------------------------------------------------- CBOR


def _to_int_keys(data: Any, mapping: dict[str, int]) -> Any:
    if not isinstance(data, dict):
        return data
    out: dict[Any, Any] = {}
    for key, value in data.items():
        if key in _NESTED and isinstance(value, dict):
            value = _to_int_keys(value, CBOR_KEYS[_NESTED[key]])
        elif key in _NESTED_LIST and isinstance(value, list):
            nested = CBOR_KEYS[_NESTED_LIST[key]]
            value = [_to_int_keys(item, nested) for item in value]
        out[mapping.get(key, key)] = value
    return out


def _from_int_keys(data: Any, mapping: dict[str, int]) -> Any:
    if not isinstance(data, dict):
        return data
    reverse = {v: k for k, v in mapping.items()}
    out: dict[str, Any] = {}
    for key, value in data.items():
        name = reverse.get(key, key) if isinstance(key, int) else key
        if name in _NESTED and isinstance(value, dict):
            value = _from_int_keys(value, CBOR_KEYS[_NESTED[name]])
        elif name in _NESTED_LIST and isinstance(value, list):
            nested = CBOR_KEYS[_NESTED_LIST[name]]
            value = [_from_int_keys(item, nested) for item in value]
        out[name] = value
    return out


def _cbor2():
    try:
        import cbor2
    except ImportError as exc:  # pragma: no cover
        raise CodecError(
            "CBOR support requires the `cbor2` package: pip install 'qonclave[cbor]'"
        ) from exc
    return cbor2


def encode_cbor(model: BaseModel, *, int_keys: bool = True) -> bytes:
    """Serialize to canonical CBOR.

    `canonical=True` is not optional in practice: signatures are computed over the encoded bytes,
    so a non-deterministic encoder produces signatures the far end cannot verify.

    `int_keys` is REQUIRED on the `minimal` profile and permitted elsewhere.
    """
    data = _dump(model)
    if int_keys:
        mapping = CBOR_KEYS.get(type(model).__name__)
        if mapping:
            data = _to_int_keys(data, mapping)
    return _cbor2().dumps(data, canonical=True)


def decode_cbor(raw: bytes, model: type[M]) -> M:
    """Deserialize CBOR, accepting either integer or string keys.

    Accepting both is deliberate: a gateway may transcode from JSON without applying the key map,
    and rejecting that would make the two encodings not actually interchangeable.
    """
    try:
        data = _cbor2().loads(raw)
        mapping = CBOR_KEYS.get(model.__name__)
        if mapping and any(isinstance(k, int) for k in data):
            data = _from_int_keys(data, mapping)
        return model.model_validate(data)
    except CodecError:
        raise
    except Exception as exc:
        raise CodecError(f"cannot decode {model.__name__} from CBOR: {exc}") from exc


# ----------------------------------------------------------------------- dispatch


def encode(model: BaseModel, encoding: str = "json") -> bytes:
    if encoding == "json":
        return encode_json(model)
    if encoding == "cbor":
        return encode_cbor(model)
    raise CodecError(f"unknown encoding {encoding!r}")


def decode(raw: bytes, model: type[M], encoding: str = "json") -> M:
    if encoding == "json":
        return decode_json(raw, model)
    if encoding == "cbor":
        return decode_cbor(raw, model)
    raise CodecError(f"unknown encoding {encoding!r}")


__all__ = [
    "CBOR_KEYS", "CodecError",
    "encode", "decode", "encode_json", "decode_json", "encode_cbor", "decode_cbor",
]
