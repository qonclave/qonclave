"""
policy.py — the app contract for the Qonclave framework.

A "policy" is what a developer declares to build a new use case on top of
the framework: given an escalated frame and its edge event metadata, decide
whether the event is verified, at what confidence, and what alert (and,
optionally, what hub->edge command) to emit. The framework's HTTP layer
(framework/server.py) drives this interface; it never encodes any
use-case-specific logic itself.
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
