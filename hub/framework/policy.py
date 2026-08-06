"""
policy.py — the app contract for the Qonclave framework.

A "policy" is what a developer declares to build a new use case on top of
the framework: given an escalated frame and its edge event metadata, decide
whether the event is verified, at what confidence, and what alert (and,
optionally, what hub->edge command) to emit. The framework's HTTP layer
(framework/server.py) drives this interface; it never encodes any
use-case-specific logic itself.

TODO(framework_scaffold merge): this reverts the SDK-lift rename that was
briefly on this branch (evaluate(image_path, event) -> evaluate(event:
EdgeEvent, image_path=None); on_sms_reply/reply_for_sms/last_sms_analysis ->
on_reply/reply_for/dashboard_state; Policy/Verdict/Notification re-exported
from qonclave.hub.policy). Merging framework_scaffold with upstream/main
(2026-08-06) found upstream had built real, tested features (investigation
flow, known-person priorities, buzzer control, pose track analysis) on top of
the OLD contract after the rename happened here, so this file went back to
the pre-lift shape to avoid losing any of that. Redo the lift against this
version — including analyze_track/track_settings/update_track_settings, which
did not exist yet when the lift last happened — the next time framework/
converges on qonclave.hub.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Notification:
    """An SMS notification request from a Policy to the framework."""
    message: str
    recipient: str


@dataclass
class Verdict:
    """Result of a policy evaluating one escalated frame."""

    verified: bool
    confidence: float | None
    alert: str
    reasoning_text: str | None = None
    reasoning_available: bool | None = None
    latency_s: float | None = None
    # App-specific wire fields merged flat into the response envelope,
    # e.g. {"identity_status": "not_enabled"}.
    extra: dict = field(default_factory=dict)


class Policy(ABC):
    """
    Base class for a Qonclave app. Subclasses declare the model, prompt,
    thresholds, and alert text for one use case (security, fall detection,
    hazard detection, ...); the framework handles transport, verification
    plumbing, and the event store around it.
    """

    name: str = "policy"

    @abstractmethod
    def evaluate(self, image_path: str, event: dict) -> Verdict:
        """Evaluate one escalated frame + its edge event metadata."""
        raise NotImplementedError

    def command_for(self, verdict: Verdict, event: dict) -> dict | None:
        """
        Optional hub->edge command to send back in the response (e.g.
        {"type": "navigate_to", "target": "living_room"}). Most apps have
        no edge actuator to command, so the default is no command.
        """
        return None

    def notify_for(self, verdict: Verdict, event: dict) -> Notification | None:
        """
        Optional SMS notification to send after a verdict. Return a
        Notification(message, recipient) to trigger an SMS; None to suppress.
        In trial mode the framework sends a fixed template to a fixed number
        regardless of the Notification's field values — they are accepted now
        and will be used in a future release.
        """
        return None

    def on_sms_reply(self, sender: str, body: str) -> dict | None:
        """
        Called when Twilio delivers an inbound SMS reply to POST /sms.
        Return an MQTT command dict to publish to the last known device, or
        None to take no action. The framework handles the MQTT publish;
        the Policy decides what the reply means.

        sender  the phone number that replied (e.g. "+15551234567")
        body    the reply text as received (leading/trailing whitespace stripped)
        """
        return None

    def reply_for_sms(self, sender: str, body: str) -> str | None:
        """
        Optional outbound reply to send back to the operator after processing
        their inbound SMS. Called by the framework immediately after
        on_sms_reply(); the framework sends the returned text as an SMS via
        SMSBus.send(). Return None to send no reply.

        sender  the phone number that replied (e.g. "+15551234567")
        body    the reply text as received (same value passed to on_sms_reply)
        """
        return None

    def last_sms_analysis(self) -> dict | None:
        """
        Optional: return the most recent LLM analysis of an inbound SMS reply,
        for display on the operator dashboard. Apps that use an LLM to
        interpret SMS replies should override this to expose the latest result.
        The framework surfaces this at GET /user/llm_response.

        Expected return shape (all fields optional):
            {"intent": str, "reply": str|None, "mqtt_command": dict|None,
             "latency_s": float|None, "from": str|None}
        """
        return None

    def analyze_track(self, track_id: int, image_bytes: bytes, face: dict | None,
                      pose: dict | None) -> dict | None:
        """Optional app-specific analysis for a per-person tracking sample."""
        return None

    def track_settings(self) -> dict | None:
        """Optional UI-tunable settings for app-specific track analysis."""
        return None

    def update_track_settings(self, values: dict) -> dict | None:
        """Validate and apply app-specific track-analysis settings."""
        return None
