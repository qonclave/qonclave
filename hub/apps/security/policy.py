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
from face_id.identity import FaceIdentityBackend

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
            extra=self._identify(image_path, person),
        )

    def _identify(self, image_path: str, person_present: bool | None) -> dict:
        """Run face identification against known_faces/, only once a person
        is confirmed present (no point detecting faces in an empty frame)."""
        if person_present is not True or self.face_id is None:
            return {"identity_status": "not_enabled"}

        face = self.face_id.identify(image_path)
        if not face.get("available"):
            return {"identity_status": "not_enabled"}
        if not face.get("face_detected"):
            return {"identity_status": "no_face_detected"}

        if face.get("identified"):
            status = f"{face['name']} ({face['confidence'] * 100:.0f}%)"
        else:
            status = "unknown"

        log.info("Face ID (%.2fs): %s", face.get("latency_s") or 0.0, status)

        return {
            "identity_status": status,
            "identity_name": face.get("name"),
            "identity_confidence": face.get("confidence"),
        }
