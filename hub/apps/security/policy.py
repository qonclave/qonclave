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
from framework.face_id.identity import FaceIdentityBackend

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

    def __init__(self, vlm: VLMBackend, face_id: FaceIdentityBackend | None = None):
        self.vlm = vlm
        self.face_id = face_id

    def evaluate(self, image_path: str, event: dict) -> Verdict:
        # Run face-ID up front — it does its own independent face detection,
        # so it doesn't need to wait on the VLM's person_present verdict —
        # and fold any known name into the verify prompt so the VLM's
        # description names the person instead of describing them generically.
        identity = self._run_face_id(image_path)
        prompt = self._build_prompt(identity)

        result = self.vlm.structured_query(
            image_path, prompt, VERIFY_MAX_NEW_TOKENS,
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
            extra=self._identity_extra(identity, person),
        )

    def _run_face_id(self, image_path: str) -> dict:
        """Run face identification against known_faces/. Always attempted
        (cheap: face_id's own detector short-circuits on no-face frames)
        so a known name is available before the VLM prompt is built."""
        if self.face_id is None:
            return {"available": False}
        return self.face_id.identify(image_path)

    def _build_prompt(self, identity: dict) -> str:
        """Verify prompt, personalized with the face-ID match (if any) so
        the VLM's description names the person instead of writing "a person"."""
        if not identity.get("identified"):
            return VERIFY_PROMPT

        name = identity["name"]
        return (
            "You are a security camera verifier. Look at the image and respond with "
            "ONLY a JSON object, no other text, of exactly this form:\n"
            '{"person_present": true or false, '
            '"confidence": a number from 0 to 1, '
            '"description": "a short description of the scene"}\n'
            f'Face recognition has matched the visible person to "{name}". If that '
            f'matches what you see, refer to them by name ("{name}") in the '
            'description instead of generic terms like "a person" or "an individual".\n'
            "Set person_present to true only if a human is clearly visible."
        )

    def _identity_extra(self, identity: dict, person_present: bool | None) -> dict:
        """Fold the already-computed face-ID result into the verdict's extra
        fields, gated on the VLM's own person_present verdict so a stray
        face-ID hit on an empty/no-person frame isn't reported as an identity."""
        if person_present is not True or not identity.get("available"):
            return {"identity_status": "not_enabled"}
        if not identity.get("face_detected"):
            return {"identity_status": "no_face_detected"}

        if identity.get("identified"):
            status = f"{identity['name']} ({identity['confidence'] * 100:.0f}%)"
        else:
            status = "unknown"

        log.info("Face ID (%.2fs): %s", identity.get("latency_s") or 0.0, status)

        return {
            "identity_status": status,
            "identity_name": identity.get("name"),
            "identity_confidence": identity.get("confidence"),
        }
