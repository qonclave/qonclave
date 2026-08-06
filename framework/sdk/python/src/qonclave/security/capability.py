"""
capability.py — the brokered-access primitive.

ARCHITECTURE.md §3 and SECURITY.md §3 already describe the hub as an authorization broker: the
edge never accepts unsolicited connections, the hub authenticates the requester and enforces RBAC,
then hands out a temporary token, after which traffic flows peer-to-peer. That is written up for
two targets — an external operator over WebRTC, and a local compute node.

Edge to *foreign hub* is the same primitive with a third target, so it is the same code path here
rather than a third special case. `AudienceKind` is the only thing that differs.

The property that matters most is **offline verification**. An audience validates a grant against
the issuer's pinned CA root, with no callback to the issuing hub. A mesh in which Hub B must reach
Hub A in order to adopt Hub A's orphaned edges has not actually failed over — which is exactly the
scenario ARCHITECTURE.md's self-healing handoff is meant to cover.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.enums import AudienceKind, Scope
from ..core.models import CapabilityGrant

log = logging.getLogger("qonclave.security.capability")


class GrantDenied(Exception):
    """A grant did not authorize what was attempted. `reason` is machine-readable."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(slots=True)
class VerificationResult:
    valid: bool
    reason: str = "ok"
    grant: CapabilityGrant | None = None

    def raise_for_status(self) -> CapabilityGrant:
        if not self.valid or self.grant is None:
            raise GrantDenied(self.reason)
        return self.grant


def _parse_ts(value: str) -> datetime:
    # Accept the trailing Z form; fromisoformat only learned it in 3.11.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify(
    grant: CapabilityGrant,
    *,
    audience_id: str,
    audience_kind: AudienceKind,
    tenant_id: str,
    required_scope: Scope,
    trusted_issuers: dict[str, bytes],
    revoked: set[str] | None = None,
    now: datetime | None = None,
    verify_signature: bool = True,
) -> VerificationResult:
    """Check whether `grant` authorizes this operation, offline.

    `trusted_issuers` maps issuer node_id to the public key pinned during hub-to-hub federation.
    An issuer absent from that map is untrusted regardless of how well-formed its grant is —
    that map is the entire trust boundary.

    Checks run cheapest-and-most-decisive first, so a constrained device rejecting a bad grant
    does the least possible work before saying no.
    """
    now = now or datetime.now(timezone.utc)
    revoked = revoked or set()

    if grant.grant_id in revoked:
        return VerificationResult(False, "revoked")

    if grant.issuer not in trusted_issuers:
        return VerificationResult(False, "untrusted_issuer")

    if grant.audience.node_id != audience_id or grant.audience.kind is not audience_kind:
        # A grant is valid for exactly one audience. Presenting a compute-node grant to a peer hub
        # must fail even though both are legitimate targets of the same subject.
        return VerificationResult(False, "wrong_audience")

    if grant.tenant_id != tenant_id and not grant.cross_tenant:
        # Deny by default. This is where SECURITY.md §2's isolation guarantee is actually
        # enforced, rather than being a property of how the network happens to be laid out.
        return VerificationResult(False, "cross_tenant_denied")

    if required_scope not in grant.scope:
        return VerificationResult(False, "scope_denied")

    try:
        if grant.not_before and now < _parse_ts(grant.not_before):
            return VerificationResult(False, "not_yet_valid")
        if now >= _parse_ts(grant.expires_at):
            return VerificationResult(False, "expired")
    except ValueError:
        return VerificationResult(False, "malformed_validity")

    if verify_signature:
        if grant.signature is None:
            return VerificationResult(False, "unsigned")
        from . import signing

        if not signing.verify_document(
            grant.model_dump(mode="json", exclude={"signature"}),
            grant.signature,
            trusted_issuers[grant.issuer],
        ):
            return VerificationResult(False, "bad_signature")

    return VerificationResult(True, "ok", grant)


def mint(
    *,
    issuer: str,
    subject: str,
    audience_kind: AudienceKind,
    audience_id: str,
    tenant_id: str,
    scope: list[Scope],
    ttl_seconds: int,
    cross_tenant: bool = False,
    signing_key: bytes | None = None,
    key_id: str | None = None,
    now: datetime | None = None,
) -> CapabilityGrant:
    """Issue a grant. Hubs only — a hub is the local CA (SECURITY.md §3).

    `ttl_seconds` should be short. The cost of a short TTL is that a roaming device re-requests;
    the cost of a long one is that revocation stops meaning anything, since a grant already in
    the field cannot be recalled from a device that is asleep.
    """
    from datetime import timedelta

    from ..core import ids

    now = now or datetime.now(timezone.utc)
    grant = CapabilityGrant(
        grant_id=ids.new_id("grant"),
        issuer=issuer,
        subject=subject,
        audience={"kind": audience_kind, "node_id": audience_id},
        tenant_id=tenant_id,
        cross_tenant=cross_tenant,
        scope=scope,
        not_before=now.isoformat(),
        expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
    )

    if cross_tenant:
        log.warning(
            "minting CROSS-TENANT grant %s: %s -> %s/%s. This crosses the isolation boundary in "
            "SECURITY.md §2 and should be rare and audited.",
            grant.grant_id, subject, audience_kind.value, audience_id,
        )

    if signing_key is not None:
        from . import signing

        grant.signature = signing.sign_document(
            grant.model_dump(mode="json", exclude={"signature"}), signing_key, key_id=key_id
        )
    return grant
