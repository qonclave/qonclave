"""
models.py — Python binding of spec/v1/json-schema/.

One model per schema document. These are a *binding*, not the definition: where a model and the
schema disagree, the model is wrong, and tests/test_spec_conformance.py is what catches it.

Design notes that are easy to get wrong:

* `extra="allow"` everywhere. Forward compatibility requires that a v1.0 node receiving a v1.3
  document preserves the fields it does not understand rather than dropping them on the floor —
  otherwise a document loses data merely by transiting an older hop.

* `EdgeEvent` accepts EITHER an absolute `timestamp` OR a device-relative `relative_time`. A
  daily-wake sensor has no trustworthy clock, so requiring absolute time would exclude the entire
  `minimal` profile.

* `TaskDescriptor.remaining_ms` is decremented in flight. Without it, each tier re-plans against
  the original deadline and the ladder blows an SLA nobody is tracking.

Spec: spec/v1/json-schema/
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from .enums import (
    AudienceKind,
    Complexity,
    DataEncoding,
    NodeType,
    Privacy,
    Profile,
    Scope,
    SignatureAlg,
    Urgency,
)

SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------------------- wire enums
#
# `Complexity` and `Urgency` are IntEnums because their ORDERING is normative —
# placement prunes candidates with `node.max_complexity >= task.complexity`. But the schema
# defines them as STRINGS ("vlm_reason", not 4), and `spec/v1/` is normative, so the wire form
# wins and the int is an implementation detail of comparison.
#
# The enums have carried `.wire` / `.from_wire` since they were written; what was missing was
# wiring them into pydantic, so every document this SDK produced serialized the raw int and
# failed its own schema. Nothing caught it because the conformance suite validates against these
# models rather than against the schemas — see tests/test_schema_validation.py, added with this
# fix, which closes that gap.


def _complexity_in(value: Any) -> Any:
    if isinstance(value, str):
        return Complexity.from_wire(value)
    return value


def _urgency_in(value: Any) -> Any:
    if isinstance(value, str):
        return Urgency.from_wire(value)
    return value


# Ints are still ACCEPTED on input, deliberately. A peer that already emits the old form should
# not become unreadable — forward compatibility is the reason `extra="allow"` exists three lines
# down, and the same argument applies here.
WireComplexity = Annotated[
    Complexity,
    BeforeValidator(_complexity_in),
    PlainSerializer(lambda c: Complexity(c).wire, return_type=str, when_used="json"),
]

WireUrgency = Annotated[
    Urgency,
    BeforeValidator(_urgency_in),
    PlainSerializer(lambda u: Urgency(u).wire, return_type=str, when_used="json"),
]


class _Doc(BaseModel):
    """Base for every wire document.

    `extra="allow"` is load-bearing, not laziness — see the module docstring.
    """

    model_config = ConfigDict(extra="allow", use_enum_values=False)


# --------------------------------------------------------------------------- shared


class RelativeTime(_Doc):
    """Time as reported by a device with no trustworthy clock.

    A sensor that deep-sleeps for a day cannot produce a reliable absolute timestamp — its RTC has
    drifted, or it has none. It reports its own wake counter and the hub anchors it.
    """

    wake_counter: int = Field(ge=0)
    ms_since_wake: int = Field(ge=0)
    uncertainty_s: int | None = Field(default=None, ge=0)


class MediaPayload(_Doc):
    """An attached sensor sample. Optional on every message — a temperature sensor has a reading,
    not a frame, and a LoRa frame could not carry one anyway."""

    media_type: str
    data_encoding: DataEncoding
    data: str


class Power(_Doc):
    """Measured power state. Placement reads this to decide whether a node should do its own
    inference; it is never declared by the application."""

    battery_pct: float | None = Field(default=None, ge=0, le=100)
    on_mains: bool | None = None
    thermal_headroom_c: float | None = None
    duty_cycle_s: int | None = Field(default=None, ge=1)

    @property
    def is_constrained(self) -> bool:
        """True when this node should be treated as energy-limited.

        Used as the default heuristic in DefaultPlacement. A node on mains is never constrained
        regardless of what its battery reports, since it is charging.
        """
        if self.on_mains:
            return False
        if self.duty_cycle_s is not None:
            return True
        return self.battery_pct is not None and self.battery_pct < 30.0

    @property
    def is_throttling(self) -> bool:
        return self.thermal_headroom_c is not None and self.thermal_headroom_c <= 0


class Load(_Doc):
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    active_tasks: int | None = Field(default=None, ge=0)
    queue_depth: int | None = Field(default=None, ge=0)


class Capabilities(_Doc):
    hardware: list[str] = Field(default_factory=list)
    supported_models: list[str] = Field(default_factory=list)
    max_complexity: WireComplexity | None = None
    transports: list[str] = Field(default_factory=list)
    encodings: list[Literal["json", "cbor"]] = Field(default_factory=list)

    def can_serve(self, complexity: Complexity) -> bool:
        """Whether a node with these capabilities can run a task of this class at all.

        Placement calls this to prune candidates *before* the developer's policy chooses, so a
        policy never has to defend against being handed an impossible tier.
        """
        if self.max_complexity is None:
            return True
        return self.max_complexity >= complexity


class Signature(_Doc):
    """Detached signature over the canonical encoding of the enclosing document, excluding this
    field. Canonical form is RFC 8785 for JSON, RFC 8949 §4.2.1 for CBOR — without one, two
    encoders differing only in key order produce signatures that fail for no visible reason."""

    alg: SignatureAlg
    key_id: str | None = None
    value: str


class TaskDescriptor(_Doc):
    """What the application DECLARES about a task, as against what the framework MEASURES about
    nodes. That split is the whole discipline of placement: a developer states intent, the
    framework supplies facts."""

    complexity: WireComplexity = Complexity.HEURISTIC
    urgency: WireUrgency = Urgency.NORMAL
    privacy: Privacy = Privacy.UNRESTRICTED
    use_case: str | None = None
    deadline_ms: int | None = Field(default=None, ge=0)
    remaining_ms: int | None = Field(default=None, ge=0)
    hops: list[str] = Field(default_factory=list)

    def spend(self, elapsed_ms: int) -> "TaskDescriptor":
        """Return a copy with `elapsed_ms` deducted from the remaining budget.

        Called by the ladder before escalating. If this is skipped, the next tier plans against a
        budget that was already spent — the failure is silent and only shows up as missed
        deadlines under load.
        """
        if self.remaining_ms is None:
            return self.model_copy()
        return self.model_copy(update={"remaining_ms": max(0, self.remaining_ms - elapsed_ms)})

    @property
    def expired(self) -> bool:
        return self.remaining_ms is not None and self.remaining_ms <= 0


# --------------------------------------------------------------------------- documents


class EdgeEvent(_Doc):
    """An observation escalated from a sensing node. COMMUNICATION.md §2."""

    schema_version: str = SCHEMA_VERSION
    event_id: str
    source_node_id: str
    tenant_id: str | None = None

    timestamp: str | None = None
    relative_time: RelativeTime | None = None
    hub_received_at: str | None = None

    trigger: str
    confidence: float | None = Field(default=None, ge=0, le=1)

    payload: MediaPayload | None = None
    task: TaskDescriptor | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    power: Power | None = None
    signature: Signature | None = None

    @model_validator(mode="after")
    def _require_some_time(self) -> "EdgeEvent":
        """An event must carry some notion of when it happened.

        `full` and `constrained` send absolute `timestamp`; `minimal` may send `relative_time`
        instead and let the hub stamp it. What is not acceptable is neither.
        """
        if self.timestamp is None and self.relative_time is None:
            raise ValueError("edge event requires either `timestamp` or `relative_time`")
        return self


class Command(_Doc):
    """An instruction from a hub to an edge node. COMMUNICATION.md §4."""

    schema_version: str = SCHEMA_VERSION
    command_id: str
    issuer_id: str
    target_id: str | None = None
    tenant_id: str | None = None

    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    issued_at: str | None = None
    expires_at: str | None = None
    signature: Signature | None = None


class NodeManifest(_Doc):
    """What a node advertises about itself. COMMUNICATION.md §1.

    Nodes on the `minimal` profile never broadcast one — they are commissioned out-of-band and
    already known to their hub, and an mDNS browse would cost more radio time than their entire
    useful exchange.
    """

    schema_version: str = SCHEMA_VERSION
    service: Literal["qonclave-node"] = "qonclave-node"
    node_id: str
    node_type: NodeType
    tenant_id: str | None = None
    profile: Profile = Profile.FULL

    endpoints: dict[str, str] = Field(default_factory=dict)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    load: Load = Field(default_factory=Load)
    power: Power | None = None

    accepts_foreign_edges: bool = False
    trusted_issuers: list[str] = Field(default_factory=list)
    last_seen: str | None = None


class Audience(_Doc):
    kind: AudienceKind
    node_id: str


class CapabilityGrant(_Doc):
    """A signed, scoped, time-boxed authorization: subject may talk to audience, for these
    operations, until this instant.

    Generalizes the Direct-Bind broker (ARCHITECTURE.md §3, SECURITY.md §3) from two targets to
    three by making the peer hub just another `AudienceKind`. The audience verifies offline
    against the issuer's pinned CA root — a mesh that needs the issuing hub online in order to
    authorize failover has not failed over.
    """

    schema_version: str = SCHEMA_VERSION
    grant_id: str
    issuer: str
    subject: str
    audience: Audience
    tenant_id: str
    cross_tenant: bool = False
    scope: list[Scope] = Field(min_length=1)
    not_before: str | None = None
    expires_at: str
    signature: Signature | None = None


class CheckinRequest(_Doc):
    """Duty-cycle uplink. Everything the device has to say, in one message.

    Spec: spec/v1/json-schema/checkin.schema.json#/$defs/request
    """

    schema_version: str = SCHEMA_VERSION
    node_id: str
    tenant_id: str | None = None
    wake_counter: int = Field(ge=0)

    events: list[EdgeEvent] = Field(default_factory=list)
    power: Power | None = None
    config_version: int | None = Field(default=None, ge=0)
    ack: list[str] = Field(default_factory=list)
    grant: CapabilityGrant | None = None
    signature: Signature | None = None


class ConfigDelta(_Doc):
    version: int = Field(ge=0)
    values: dict[str, Any] = Field(default_factory=dict)


class CheckinResponse(_Doc):
    """Duty-cycle downlink. Everything the hub has been holding since last contact.

    Spec: spec/v1/json-schema/checkin.schema.json#/$defs/response
    """

    schema_version: str = SCHEMA_VERSION
    server_time: str
    accepted: list[str] = Field(default_factory=list)
    commands: list[Command] = Field(default_factory=list)
    config: ConfigDelta | None = None
    next_checkin_s: int | None = Field(default=None, ge=1)
    signature: Signature | None = None


class ArchiveRecord(_Doc):
    """A finalized event committed to long-term storage. COMMUNICATION.md §7."""

    schema_version: str = SCHEMA_VERSION
    event_id: str
    tenant_id: str
    timestamp: str
    event_data: EdgeEvent
    decision: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
    retention: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "SCHEMA_VERSION",
    "RelativeTime", "MediaPayload", "Power", "Load", "Capabilities", "Signature",
    "TaskDescriptor", "EdgeEvent", "Command", "NodeManifest", "Audience",
    "CapabilityGrant", "CheckinRequest", "CheckinResponse", "ConfigDelta", "ArchiveRecord",
]
