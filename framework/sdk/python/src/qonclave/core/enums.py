"""
enums.py — the closed value sets from spec/v1/dictionary.md.

These are normative: a receiver MUST reject a value outside a closed set. Open sets (`trigger`,
`action`) are deliberately plain strings, because applications define their own and the framework
never interprets them.

Spec: spec/v1/dictionary.md
"""

from __future__ import annotations

from enum import Enum, IntEnum


class NodeType(str, Enum):
    """Role a node currently fills. This is state, not identity — a node may be promoted or
    demoted at runtime by hub election (ARCHITECTURE.md §3)."""

    EDGE = "edge"
    HUB = "hub"
    COMPUTE = "compute"
    ARCHIVE = "archive"


class Profile(str, Enum):
    """Conformance profile. Determines which obligations a node is judged against.

    See spec/v1/profiles/. The boundary between FULL and CONSTRAINED is whether the device can
    hold a connection; between CONSTRAINED and MINIMAL, whether it is awake.
    """

    FULL = "full"
    CONSTRAINED = "constrained"
    MINIMAL = "minimal"


class Complexity(IntEnum):
    """Class of model a task needs.

    IntEnum because the ordering is *normative*, not cosmetic: placement filters candidate tiers
    with `node.max_complexity >= task.complexity`, so a node advertising DETECT is correctly
    excluded from a VLM_REASON task before any policy gets to choose.
    """

    HEURISTIC = 0
    DETECT = 1
    CLASSIFY = 2
    EMBED = 3
    VLM_REASON = 4
    LLM_REASON = 5

    @property
    def wire(self) -> str:
        return self.name.lower()

    @classmethod
    def from_wire(cls, value: str) -> "Complexity":
        return cls[value.upper()]


class Urgency(IntEnum):
    """How much latency matters. Ordered so a policy can compare."""

    BACKGROUND = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3

    @property
    def wire(self) -> str:
        return self.name.lower()

    @classmethod
    def from_wire(cls, value: str) -> "Urgency":
        return cls[value.upper()]


class Privacy(str, Enum):
    """Egress constraint.

    Enforced by the framework in placement/ladder.py, NOT by the developer's placement policy.
    A policy returning a denied tier is overridden — the isolation guarantee in SECURITY.md §2
    cannot depend on every application author getting it right.
    """

    UNRESTRICTED = "unrestricted"
    NO_EGRESS = "no_egress"
    """MUST NOT reach a shared multi-tenant compute node."""

    LOCAL_ONLY = "local_only"
    """MUST NOT leave the originating device."""


class DataEncoding(str, Enum):
    BASE64 = "base64"
    """RFC 4648 §4 with padding. The only option in JSON."""

    RAW = "raw"
    """CBOR byte string. Invalid in JSON."""


class SignatureAlg(str, Enum):
    ED25519 = "ed25519"
    ES256 = "es256"
    HS256 = "hs256"
    """PSK-derived symmetric. The constrained/minimal path, where an asymmetric handshake costs
    more energy than the message it protects."""


class Scope(str, Enum):
    """Operations a capability grant may authorize."""

    POST_EVENTS = "post_events"
    CHECKIN = "checkin"
    STREAM = "stream"
    RECOGNIZE = "recognize"
    INFER = "infer"
    SUBSCRIBE_COMMANDS = "subscribe_commands"


class AudienceKind(str, Enum):
    """What a grant authorizes the subject to talk to.

    The three targets of the same brokered-access primitive: an external viewer, an inference
    node, or a peer hub. Adding PEER-hub as a value rather than a new mechanism is the point.
    """

    OPERATOR = "operator"
    COMPUTE = "compute"
    HUB = "hub"


class TaskStatus(str, Enum):
    OK = "ok"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    UNSUPPORTED_MODEL = "unsupported_model"
    OVERLOADED = "overloaded"
    DENIED = "denied"
    ERROR = "error"


# Conventional (open) values. Reusing these keeps dashboards and archives comparable across apps,
# but nothing in the framework depends on them.
COMMON_TRIGGERS = (
    "motion_detected",
    "person_detected",
    "sound_detected",
    "threshold_crossed",
    "door_opened",
    "fall_detected",
    "heartbeat",
    "manual",
)

COMMON_ACTIONS = (
    "lock_door",
    "unlock_door",
    "navigate_to",
    "dispatch",
    "set_led",
    "capture_frame",
    "set_threshold",
    "sleep_until",
)
