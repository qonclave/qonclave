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

from framework.policy import Policy, Verdict, Notification
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
        so known names are available before the VLM prompt is built. Detects
        every face in the frame, not just the most prominent one."""
        if self.face_id is None:
            return {"available": False}
        return self.face_id.identify_all(image_path)

    @staticmethod
    def _identified_names(identity: dict) -> list[str]:
        """Distinct known names across all detected faces, best-confidence first."""
        faces = sorted(
            (f for f in identity.get("faces", []) if f.get("identified")),
            key=lambda f: -(f.get("confidence") or 0.0),
        )
        seen, names = set(), []
        for f in faces:
            if f["name"] not in seen:
                seen.add(f["name"])
                names.append(f["name"])
        return names

    def _build_prompt(self, identity: dict) -> str:
        """Verify prompt, personalized with the face-ID matches (if any) so
        the VLM's description names the people instead of writing "a person"."""
        names = self._identified_names(identity)
        if not names:
            return VERIFY_PROMPT

        if len(names) == 1:
            who = f'"{names[0]}"'
            phrasing = f'the visible person to {who}'
        else:
            who = ", ".join(f'"{n}"' for n in names)
            phrasing = f'visible people to {who}'

        return (
            "You are a security camera verifier. Look at the image and respond with "
            "ONLY a JSON object, no other text, of exactly this form:\n"
            '{"person_present": true or false, '
            '"confidence": a number from 0 to 1, '
            '"description": "a short description of the scene"}\n'
            f'Face recognition has matched {phrasing}. If that matches what you '
            f'see, refer to them by name ({who}) in the description instead of '
            'generic terms like "a person" or "an individual".\n'
            "Set person_present to true only if a human is clearly visible."
        )

    def _identity_extra(self, identity: dict, person_present: bool | None) -> dict:
        """Fold the already-computed face-ID result into the verdict's extra
        fields, gated on the VLM's own person_present verdict so a stray
        face-ID hit on an empty/no-person frame isn't reported as an identity.

        Reports every detected face: identity_status summarizes them all, while
        identity_name/identity_confidence carry the best known match (if any)
        for the dashboard's name pill."""
        faces = identity.get("faces", [])
        if person_present is not True or not identity.get("available"):
            return {"identity_status": "not_enabled"}
        if not faces:
            return {"identity_status": "no_face_detected"}

        # Per-face label, best detector-confidence first (get_embeddings order).
        parts = []
        for f in faces:
            if f.get("identified"):
                parts.append(f"{f['name']} ({f['confidence'] * 100:.0f}%)")
            else:
                parts.append("unknown")

        names = self._identified_names(identity)
        n = len(faces)
        summary = ", ".join(parts)
        status = summary if n == 1 else f"{n} faces: {summary}"

        # Best known match drives the dashboard name pill.
        best = next(
            (f for f in sorted(faces, key=lambda x: -(x.get("confidence") or 0.0))
             if f.get("identified")),
            None,
        )

        log.info("Face ID (%.2fs): %s", identity.get("latency_s") or 0.0, status)

        return {
            "identity_status": status,
            "identity_name": best["name"] if best else None,
            "identity_confidence": best["confidence"] if best else None,
            "identity_face_count": n,
            "identity_known_count": len(names),
            "identity_unknown_count": sum(1 for f in faces if not f.get("identified")),
        }

    def notify_for(self, verdict: Verdict, event: dict) -> Notification | None:
        if verdict.verified:
            return Notification(
                message=verdict.alert,
                recipient=event.get("device_id", "unknown"),
            )
        return None
