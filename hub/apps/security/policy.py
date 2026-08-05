"""
policy.py — the security (person-detection) app, built on the Qonclave
framework.

Declares what's specific to this use case: the verification prompt, the
JSON schema asked of the VLM, confidence/alert-text mapping, and the
identity_status stretch-goal stub. Everything else (transport, event store,
HTTP routes) is generic framework code.
"""

from __future__ import annotations

import json
import logging
import threading

from framework.policy import Policy, Verdict, Notification
from framework.vlm import VLMBackend
from framework.llm import LLMBackend
from framework.sms_bus import SMSBus
from framework.face_id.identity import FaceIdentityBackend
from .posture import PostureStateMachine

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

# System prompt given to the LLM when reasoning about an operator SMS reply.
_SMS_SYSTEM_PROMPT = (
    "You are the response module of a physical security hub. "
    "You have exactly two actions available: publish an MQTT command to the edge device, "
    "or send an SMS reply to the operator. You cannot call emergency services, contact "
    "anyone, or take any other action outside these two.\n\n"
    "An operator has replied to a security alert SMS. Decide:\n"
    "  1. What the operator intends (dispatch / acknowledge / query / unknown)\n"
    "  2. Whether to publish an MQTT command to the edge device\n"
    "  3. What to reply — always grounded in what you did or cannot do\n\n"
    "Reply rules:\n"
    "  - For dispatch: confirm the MQTT command was sent to the device\n"
    "  - For acknowledge: confirm the alert is logged, no further action taken\n"
    "  - For query: answer only from the event context provided; if the question "
    "is outside your capabilities (e.g. 'call 911'), state clearly that you cannot "
    "do that and suggest the operator act directly\n"
    "  - Always write as the hub in first person ('Alert is logged.', "
    "'Dispatch command sent to device.', 'I cannot call 911 — please contact "
    "emergency services directly.')\n"
    "  - Keep replies under 160 characters (one SMS)\n"
    "  - Never repeat or paraphrase the operator's message\n\n"
    "Respond with ONLY a JSON object in exactly this form:\n"
    '{"intent": "<dispatch|acknowledge|query|unknown>", '
    '"mqtt_command": null or {"type": "dispatch", "source": "sms_reply"}, '
    '"reply": "<hub response under 160 chars>"}'
)


class SecurityPolicy(Policy):
    """Stationary person detection with hub-side VLM verification."""

    name = "security"

    def __init__(self, vlm: VLMBackend, face_id: FaceIdentityBackend | None = None,
                 sms: SMSBus | None = None, llm: LLMBackend | None = None):
        self.vlm = vlm
        self.face_id = face_id
        self.sms = sms
        self.llm = llm
        self.posture = PostureStateMachine()
        # Last verified verdict stored so it can be injected into LLM context
        # for richer SMS reply reasoning.
        self._last_verdict: Verdict | None = None
        # One LLM call per inbound message, shared between on_sms_reply() and
        # reply_for_sms() which the framework calls back-to-back. Keyed by
        # (sender, body); holds the most recent result only.
        self._llm_cache: tuple[str, str, dict] | None = None  # (sender, body, result)
        self._llm_cache_lock = threading.Lock()

    def analyze_track(self, track_id, image_bytes, face, pose):
        # Posture monitoring is deliberately limited to enrolled identities.
        # Unknown/no-face tracks remain visible in the security dashboard but
        # never enter the posture state machine.
        if not face or face.get("status") != "known" or not face.get("identity"):
            return None
        return self.posture.analyze(track_id, image_bytes, face, pose)

    def track_settings(self):
        return self.posture.settings_dict()

    def update_track_settings(self, values):
        return self.posture.update_settings(values)

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
            phrasing = f'the visible person as {who}'
            requirement = (
                f'the description MUST refer to them by name, {who}, and MUST NOT '
                'use generic terms such as "a person", "an individual", "someone", '
                'or "a man"/"a woman" in place of the name'
            )
        else:
            who = ", ".join(f'"{n}"' for n in names)
            phrasing = f'visible people as {who}'
            requirement = (
                f'the description MUST refer to each of them by name ({who}) and '
                'MUST NOT use generic terms such as "a person", "an individual", '
                '"someone", or "people" in place of their names'
            )

        return (
            "You are a security camera verifier. Look at the image and respond with "
            "ONLY a JSON object, no other text, of exactly this form:\n"
            '{"person_present": true or false, '
            '"confidence": a number from 0 to 1, '
            '"description": "a short description of the scene"}\n'
            f'A trusted face-recognition system has already run on this exact frame '
            f'and identified {phrasing} — this is a fact about the image, not a '
            f'guess for you to double-check. If person_present is true, {requirement}.\n'
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
            self._last_verdict = verdict
            reasoning = verdict.reasoning_text or ""
            message = f"{verdict.alert}. {reasoning}".strip() if reasoning else verdict.alert
            return Notification(
                message=message,
                recipient=event.get("device_id", "unknown"),
            )
        return None

    # --- SMS reply handling -------------------------------------------------

    def _llm_interpret_reply(self, sender: str, body: str) -> dict:
        """
        Run (or return cached) LLM interpretation of an inbound SMS reply.
        Called from both on_sms_reply() and reply_for_sms() so the model runs
        exactly once per inbound message regardless of call order.

        Returns a parsed dict with keys: intent, mqtt_command, reply.
        Falls back to {"intent": "unknown", "mqtt_command": None, "reply": None}
        if the LLM is unavailable or returns unparseable output.
        """
        with self._llm_cache_lock:
            if (self._llm_cache is not None
                    and self._llm_cache[0] == sender
                    and self._llm_cache[1] == body):
                return self._llm_cache[2]

        fallback = {"intent": "unknown", "mqtt_command": None, "reply": None}

        if self.llm is None or not self.llm.is_available():
            log.info("LLM unavailable for SMS reply interpretation — returning fallback")
            with self._llm_cache_lock:
                self._llm_cache = (sender, body, fallback)
            return fallback

        # Build a context-rich prompt using last known event state.
        verdict = self._last_verdict
        if verdict is not None:
            event_context = (
                f"Last security event: {verdict.alert}. "
                f"Verified: {verdict.verified}. "
                f"Confidence: {verdict.confidence}. "
                f"Details: {verdict.reasoning_text or 'none'}."
            )
        else:
            event_context = "No security event has been verified in this session yet."

        prompt = (
            f"Security event context: {event_context}\n\n"
            f"Operator reply (from {sender}): {body!r}\n\n"
            "Interpret this reply and respond in the JSON format described. /no_think"
        )

        result = self.llm.generate(prompt, system=_SMS_SYSTEM_PROMPT, max_new_tokens=300)
        parsed = fallback.copy()
        if result.get("available") and result.get("text"):
            try:
                text = result["text"].strip()
                # Qwen3 wraps reasoning in <think>...</think> before the answer.
                # Strip that block so the JSON search doesn't land inside it.
                think_end = text.rfind("</think>")
                if think_end != -1:
                    text = text[think_end + len("</think>"):].strip()
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    obj = json.loads(text[start:end + 1])
                    parsed["intent"] = obj.get("intent", "unknown")
                    parsed["mqtt_command"] = obj.get("mqtt_command")
                    parsed["reply"] = obj.get("reply")
            except (ValueError, TypeError) as e:
                log.warning("LLM SMS reply parse failed: %s — raw: %r", e, result.get("text"))

        log.info("LLM SMS interpretation (%.2fs): intent=%s command=%s reply=%r",
                 result.get("latency_s") or 0.0,
                 parsed["intent"], parsed["mqtt_command"],
                 (parsed["reply"] or "")[:60])
        log.debug("LLM SMS raw output: %r", (result.get("text") or "")[:300])

        with self._llm_cache_lock:
            self._llm_cache = (sender, body, parsed)
        return parsed

    def on_sms_reply(self, sender: str, body: str) -> dict | None:
        keyword = body.strip().upper()

        if keyword == "STOP":
            if self.sms is not None:
                self.sms.suppress()
            log.info("SMS reply STOP from %s — outbound SMS suppressed for this session", sender)
            return None

        if keyword == "DISPATCH":
            log.info("SMS reply DISPATCH from %s — publishing dispatch command", sender)
            return {"type": "dispatch", "source": "sms_reply", "requested_by": sender}

        # Unrecognised keyword — ask the LLM.
        parsed = self._llm_interpret_reply(sender, body)
        command = parsed.get("mqtt_command")
        if command:
            log.info("LLM SMS intent=%s -> MQTT command %s", parsed.get("intent"), command)
            return command

        log.info("LLM SMS intent=%s, no command for reply %r from %s",
                 parsed.get("intent"), body, sender)
        return None

    def reply_for_sms(self, sender: str, body: str) -> str | None:
        keyword = body.strip().upper()
        # Keywords are handled entirely in on_sms_reply; no LLM reply needed.
        if keyword in ("STOP", "DISPATCH"):
            return None

        parsed = self._llm_interpret_reply(sender, body)
        reply = parsed.get("reply")
        return reply if reply else None

    def last_sms_analysis(self) -> dict | None:
        with self._llm_cache_lock:
            if self._llm_cache is None:
                return None
            sender, body, result = self._llm_cache
        return {
            "from": sender,
            "message": body,
            "intent": result.get("intent"),
            "reply": result.get("reply"),
            "mqtt_command": result.get("mqtt_command"),
            "latency_s": result.get("latency_s"),
        }
