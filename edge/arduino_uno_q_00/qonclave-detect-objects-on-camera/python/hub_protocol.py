# SPDX-License-Identifier: MPL-2.0

"""
hub_protocol.py — this device's half of the Qonclave wire contract.

Kept out of main.py so it can be tested off-device: main.py imports
`arduino.app_utils`, which only exists on the UNO Q.

Deliberately hand-written against spec/v1 rather than importing the qonclave
SDK. The protocol is small enough that hand-writing it costs less than carrying
a Python dependency onto flashed firmware, and the spec exists precisely so the
smallest devices can implement it directly — see spec/v1/README.md. The price is
that this file and the schema can drift, which is what the tests are for.

Reference:
    spec/v1/json-schema/edge-event.schema.json
    spec/v1/json-schema/command.schema.json
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, UTC

SCHEMA_VERSION = "1.0"


def build_edge_event(*, node_id: str, trigger: str,
                     confidence: float | None = None,
                     frame: bytes | None = None,
                     metadata: dict | None = None,
                     event_id: str | None = None,
                     timestamp: str | None = None) -> dict:
    """One edge event in spec/v1 shape.

    `frame` travels base64 inside the document (decision D3). That costs about a
    third more bytes than streaming it as the raw HTTP body, which is irrelevant
    over LAN WiFi and buys one self-describing document instead of a body plus a
    query string.

    `timestamp` is omitted when this device has no trustworthy clock; the hub
    stamps it on receipt and the schema permits either. The UNO Q's Linux side
    has a real clock, so it normally supplies one.
    """
    event: dict = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or f"{node_id}-{uuid.uuid4().hex[:8]}",
        "source_node_id": node_id,
        "trigger": trigger,
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
    }
    if confidence is not None:
        event["confidence"] = confidence
    if metadata:
        event["metadata"] = dict(metadata)
    if frame:
        event["payload"] = {
            "media_type": "image/jpeg",
            "data_encoding": "base64",
            "data": base64.b64encode(frame).decode("ascii"),
        }
    return event


def normalize_command(command) -> dict | None:
    """Flatten either command shape into {"action": ..., **params}.

    The spec nests arguments under `parameters` beside an `action`; the pre-spec
    form put a `type` at the top level with the arguments beside it. The hub
    emits both in one payload during the migration, so accepting both here means
    this device works against an updated hub and an un-updated one alike.

    Returns None for anything unusable, so the caller has one check rather than
    a chain of isinstance guards.
    """
    if not isinstance(command, dict):
        return None
    action = command.get("action") or command.get("type")
    if not action:
        return None
    params = command.get("parameters")
    if not isinstance(params, dict):
        # Pre-spec form: arguments sit beside `type` rather than nested.
        params = command
    return {**params, "action": action}


def command_expired(command, *, now: datetime | None = None) -> bool:
    """Whether a command's `expires_at` has passed.

    CONVENTIONS.md section 5 puts this below the application layer deliberately:
    anything time-sensitive carries expires_at and the decoder drops it, rather
    than trusting each firmware author to remember. A robot_move delivered a day
    late from a mailbox is a robot that moves for no reason.

    An unparseable expires_at counts as NOT expired. Refusing to act on a
    malformed timestamp would let one bad field from the hub disable the device
    entirely, which is a worse failure than acting on a stale command.
    """
    if not isinstance(command, dict):
        return False
    raw = command.get("expires_at")
    if not raw:
        return False
    try:
        expires = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) >= expires
