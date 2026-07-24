"""
policy.py — the security (person-detection) app, built on the Qonclave
framework.

Declares what's specific to this use case: the verification prompt, the
JSON schema asked of the VLM, confidence/alert-text mapping, and the
identity_status stretch-goal stub. Everything else (transport, event store,
HTTP routes) is generic framework code.
"""

from __future__ import annotations

import logging

from framework.policy import Policy, Verdict
from framework.vlm import VLMBackend

log = logging.getLogger("qonclave.hub")

# Structured verification: ask for a strict JSON object so we parse fields
# instead of keyword-matching prose.
VERIFY_PROMPT = (
    "You are a security camera verifier. Look at the image and respond with "
    "ONLY a JSON object, no other text, of exactly this form:\n"
    '{"person_present": true or false, '
    '"confidence": a number from 0 to 1, '
    '"description": "a short description of the scene"}\n'
    "Set person_present to true only if a human is clearly visible."
)
VERIFY_MAX_NEW_TOKENS = 128


class SecurityPolicy(Policy):
    """Stationary person detection with hub-side VLM verification."""

    name = "security"

    def __init__(self, vlm: VLMBackend):
        self.vlm = vlm

    def evaluate(self, image_path: str, event: dict) -> Verdict:
        result = self.vlm.structured_query(
            image_path, VERIFY_PROMPT, VERIFY_MAX_NEW_TOKENS,
            json_mode=True, temperature=0.1,
        )

        if not result.get("available"):
            return Verdict(
                verified=False, confidence=None,
                alert="unverified (reasoning unavailable on this hub)",
                reasoning_text=None, reasoning_available=False,
                latency_s=None,
                extra={"identity_status": "not_enabled"},
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

        if person is True:
            alert = "Person verified near camera"
        elif person is False:
            alert = "No person confirmed in frame"
        else:
            alert = "Verification inconclusive"

        log.info("SecurityPolicy verify (%.2fs): person=%s conf=%s",
                 result.get("latency_s") or 0.0, person, conf)

        return Verdict(
            verified=bool(person),
            confidence=conf,
            alert=alert,
            reasoning_text=description or result.get("text"),
            reasoning_available=True,
            latency_s=result.get("latency_s"),
            extra={"identity_status": "not_enabled"},  # stretch: known/unknown face
        )
