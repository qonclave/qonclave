"""
policy.py — the application contract.

A Policy is what a developer declares to build a use case on the framework: given an escalated
event, decide whether it is verified, at what confidence, and what should happen as a result. The
framework's HTTP layer drives this interface and never encodes use-case logic itself.

Ported from hub/framework/policy.py, with two changes:

* `evaluate` takes an `EdgeEvent` model rather than a raw dict, so a Policy sees a validated
  document rather than whatever happened to arrive.
* Backends arrive via the constructor rather than the call signature. The old signature grew a
  parameter every time a capability was added (`vlm`, `llm`, `face_id`, ...), which meant every
  Policy in existence had to change to gain a capability it did not use.

Nothing here knows what "person_present" or "fall_detected" means. That is the point:
apps/<name>/ declares meaning, framework/ moves bytes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.models import Command, EdgeEvent


@dataclass(slots=True)
class Notification:
    """An outbound alert a Policy wants sent to a human.

    The hub is the only node permitted egress (SECURITY.md §1), and this is how a Policy asks it
    to exercise that. The transport — SMS, webhook, Slack — is deployment configuration, not the
    Policy's concern.
    """

    message: str
    recipient: str | None = None
    urgency: str = "normal"


@dataclass(slots=True)
class Verdict:
    """The result of evaluating one escalated event."""

    verified: bool
    confidence: float | None = None
    alert: str = ""
    reasoning_text: str | None = None
    reasoning_available: bool | None = None
    latency_s: float | None = None

    extra: dict[str, Any] = field(default_factory=dict)
    """App-specific fields merged flat into the response envelope, e.g. identity_status."""


class Policy(ABC):
    """Base class for a Qonclave application.

    Subclasses declare the prompt, thresholds, and alert semantics for one use case. The framework
    handles transport, placement, verification plumbing, and the event store around it.
    """

    name: str = "policy"

    @abstractmethod
    def evaluate(self, event: EdgeEvent, image_path: str | None = None) -> Verdict:
        """Evaluate one escalated event.

        `image_path` is a local path to the decoded payload, if the event carried one. It is None
        for payload-free events — a threshold crossing from a duty-cycled sensor is a real event
        with nothing to look at, and a Policy that assumes an image will crash on the smallest
        devices in the fleet.
        """
        raise NotImplementedError

    def command_for(self, verdict: Verdict, event: EdgeEvent) -> Command | None:
        """Optional command to send back to the originating device.

        Returned commands are both included in the HTTP response and queued to the device's
        mailbox, so a device that is not holding a connection still receives them on next contact.

        Set `expires_at` on anything time-sensitive. A duty-cycled device may not wake for a day,
        and an unlock command that fires a day late is a security incident rather than a slow
        delivery.
        """
        return None

    def notify_for(self, verdict: Verdict, event: EdgeEvent) -> Notification | None:
        """Optional human-facing alert. Default suppresses all notifications."""
        return None

    def on_reply(self, sender: str, body: str) -> Command | None:
        """Handle an inbound operator reply (SMS, chat). Return a command to dispatch, or None."""
        return None

    def reply_for(self, sender: str, body: str) -> str | None:
        """Optional text to send back after `on_reply`."""
        return None

    def analyze_track(self, track_id: int, image_bytes: bytes, face: dict[str, Any] | None,
                      pose: dict[str, Any] | None) -> dict[str, Any] | None:
        """Optional app-specific analysis for a per-person tracking sample.

        Called once per /track/analyze request that already ran the framework's
        own face/pose analyzers; face and pose are their results (None if that
        analyzer wasn't requested or is unavailable). Return value is merged
        into the response as-is; the framework doesn't interpret it.
        """
        return None

    def track_settings(self) -> dict[str, Any] | None:
        """Optional UI-tunable settings for app-specific track analysis.

        Returning None — the default — means the app has no such settings,
        matching dashboard_state's "nothing yet" convention.
        """
        return None

    def update_track_settings(self, values: dict[str, Any]) -> dict[str, Any] | None:
        """Validate and apply app-specific track-analysis settings.

        `values` is the raw request body; a Policy that doesn't implement
        this returns None and the caller reports the setting as unsupported."""
        return None

    def dashboard_state(self) -> dict[str, Any] | None:
        """Optional app-specific state for the operator UI.

        The framework serves whatever this returns without interpreting it, which
        is the point: an app that reasons about operator replies, tracks a
        occupancy count, or holds a calibration state has somewhere to surface it
        without the framework growing a method per use case.

        Returning None — the default — means the app has no such state, and the
        framework reports that rather than an empty object, so a UI can tell
        "nothing yet" from "not applicable".
        """
        return None
