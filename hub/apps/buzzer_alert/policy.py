"""
policy.py — Buzzer alert control app built on the Qonclave framework.

Allows manual Hub-triggered start and stop of an edge device's Modulino Buzzer,
with customizable tone/frequency programming and operator dashboard controls.
"""

from __future__ import annotations

import logging
from framework.policy import Policy, Verdict, Notification
from framework.vlm import VLMBackend

log = logging.getLogger("qonclave.hub")

VERIFY_PROMPT = (
    "You are a security verifier. Look at the image and respond with "
    "ONLY a JSON object of form:\n"
    '{"person_present": true or false, '
    '"confidence": a number from 0 to 1, '
    '"description": "a short description of the scene"}\n'
)

class BuzzerAlertPolicy(Policy):
    """Buzzer alert app policy with manual Start/Stop and custom tone control."""

    name = "buzzer_alert"

    def __init__(self, vlm: VLMBackend, target_device_id: str = "buzzer-01", auto_trigger: bool = False):
        self.vlm = vlm
        self.target_device_id = target_device_id
        self.auto_trigger = auto_trigger

    def evaluate(self, image_path: str, event: dict) -> Verdict:
        result = self.vlm.structured_query(
            image_path, VERIFY_PROMPT, max_new_tokens=128, json_mode=True, temperature=0.1
        )
        if not result.get("available"):
            return Verdict(
                verified=False,
                confidence=None,
                alert="unverified (reasoning unavailable)",
                reasoning_available=False,
            )

        parsed = result.get("parsed") or {}
        person = parsed.get("person_present")
        if isinstance(person, str):
            person = person.strip().lower() in ("true", "yes", "1")
        conf = parsed.get("confidence")
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None

        description = parsed.get("description")
        alert = "Person verified near camera" if person else "No person confirmed"

        return Verdict(
            verified=bool(person),
            confidence=conf,
            alert=alert,
            reasoning_text=description or result.get("text"),
            reasoning_available=True,
            latency_s=result.get("latency_s"),
        )

    def command_for(self, verdict: Verdict, event: dict) -> dict | None:
        """
        If auto_trigger is True and a person is verified, automatically emit a buzzer start command.
        In manual mode (default), returns None so buzzer is triggered via /user/buzzer-command.
        """
        if self.auto_trigger and verdict.verified:
            return {
                "type": "buzzer",
                "action": "start",
                "frequency": 880,  # 880Hz warning tone
                "duration": 0,
            }
        return None
