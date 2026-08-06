"""
qonclave.security — identity, transport security, tenancy, and brokered access.

Two crypto paths on purpose. `full`-profile nodes use mTLS; `constrained` and `minimal` use a
PSK established at out-of-band commissioning, because a full handshake on an ESP32 costs more
energy than the message it protects (SECURITY.md §3).
"""

from .capability import GrantDenied, VerificationResult, mint, verify
from .signing import canonicalize, sign_document, verify_document

__all__ = [
    "mint", "verify", "VerificationResult", "GrantDenied",
    "sign_document", "verify_document", "canonicalize",
]
