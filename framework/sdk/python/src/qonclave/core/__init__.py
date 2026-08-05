"""
qonclave.core — the layer every role imports.

Depends on nothing else in qonclave, and on nothing heavier than pydantic. That constraint is
what lets a constrained edge install carry core without dragging in a web framework, and it is
enforced by tests/test_layering.py rather than left to good intentions.
"""

from .enums import (
    AudienceKind, Complexity, DataEncoding, NodeType, Privacy, Profile,
    Scope, SignatureAlg, TaskStatus, Urgency,
)
from .models import (
    SCHEMA_VERSION, ArchiveRecord, Audience, Capabilities, CapabilityGrant,
    CheckinRequest, CheckinResponse, Command, ConfigDelta, EdgeEvent, Load,
    MediaPayload, NodeManifest, Power, RelativeTime, Signature, TaskDescriptor,
)

__all__ = [
    "SCHEMA_VERSION",
    "NodeType", "Profile", "Complexity", "Urgency", "Privacy",
    "DataEncoding", "SignatureAlg", "Scope", "AudienceKind", "TaskStatus",
    "RelativeTime", "MediaPayload", "Power", "Load", "Capabilities", "Signature",
    "TaskDescriptor", "EdgeEvent", "Command", "NodeManifest", "Audience",
    "CapabilityGrant", "CheckinRequest", "CheckinResponse", "ConfigDelta", "ArchiveRecord",
]
