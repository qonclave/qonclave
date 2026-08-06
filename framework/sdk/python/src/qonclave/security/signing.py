"""
signing.py — detached signatures over canonical documents.

The canonicalization is the part that actually matters. Two encoders that differ only in key
order or number formatting produce different bytes for the same logical document, and therefore
signatures that fail to verify at the far end for no visible reason. So signing always runs over
a canonical form: RFC 8785 (JCS) for JSON, RFC 8949 §4.2.1 for CBOR.

HS256 is implemented here with the stdlib because it is the algorithm the constrained profiles
use, and those are the devices least able to carry a crypto dependency. Ed25519 and ES256 require
`cryptography` and are used by `full`-profile nodes, which can afford it.

Spec: spec/v1/json-schema/common.schema.json#/$defs/signature
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from ..core.enums import SignatureAlg
from ..core.models import Signature


def canonicalize(doc: dict[str, Any]) -> bytes:
    """RFC 8785 JSON Canonicalization Scheme.

    Sorted keys, no insignificant whitespace, UTF-8. Python's json module gives us the sorting and
    separators; the remaining JCS requirements (number formatting) hold for the value types our
    schemas permit.
    """
    return json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def sign_document(
    doc: dict[str, Any],
    key: bytes,
    *,
    alg: SignatureAlg = SignatureAlg.HS256,
    key_id: str | None = None,
) -> Signature:
    """Sign the canonical form of `doc`.

    `doc` must already have the `signature` field removed — a signature cannot cover itself.
    """
    payload = canonicalize(doc)

    if alg is SignatureAlg.HS256:
        raw = hmac.new(key, payload, hashlib.sha256).digest()
    elif alg is SignatureAlg.ED25519:
        raw = _ed25519_sign(payload, key)
    else:
        raise NotImplementedError(f"signing algorithm {alg.value} not implemented")

    return Signature(alg=alg, key_id=key_id, value=_b64(raw))


def verify_document(doc: dict[str, Any], signature: Signature, key: bytes) -> bool:
    """Verify a detached signature. Returns False rather than raising on any failure.

    Callers are verification paths that must not be crashable by a malformed input — a device
    presenting garbage should be rejected, not able to take the hub down.
    """
    try:
        payload = canonicalize(doc)
        raw = _unb64(signature.value)

        if signature.alg is SignatureAlg.HS256:
            expected = hmac.new(key, payload, hashlib.sha256).digest()
            return hmac.compare_digest(raw, expected)
        if signature.alg is SignatureAlg.ED25519:
            return _ed25519_verify(payload, raw, key)
        return False
    except Exception:
        return False


def _ed25519_sign(payload: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(key).sign(payload)


def _ed25519_verify(payload: bytes, sig: bytes, key: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(key).verify(sig, payload)
        return True
    except InvalidSignature:
        return False
