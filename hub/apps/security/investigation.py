"""Event-driven VLM investigation for the security app.

The VLM no longer runs on a timer whenever a person is visible. Instead the
posture state machine (posture.py) drives a small investigation state machine:

    MONITORING -> WAITING_FOR_CAPTURE -> VLM_RUNNING -> COOLDOWN -> MONITORING

A posture sample whose state is SUSPICIOUS/DANGER and whose abnormal timer
(the state machine's own abnormal_duration) has run for at least
``trigger_persistence_seconds`` opens exactly ONE investigation event:

    1. the last ~10 received person crops (kept at ~1 fps) are frozen as
       evidence for that event,
    2. a ``capture_investigation_image`` command is published to the edge
       device over MQTT, asking for one fresh full-resolution frame,
    3. the VLM runs ONCE, on the captured frame when it arrives (or on the
       best buffered crop after ``capture_timeout_seconds``), with the
       posture context folded into the prompt,
    4. the classification routes at most ONE SMS
       (EMERGENCY_LIKELY -> emergency, UNCERTAIN -> manual check,
       SAFE_LIKELY -> none),
    5. COOLDOWN suppresses new events for ``cooldown_seconds`` AND until the
       person has been observed NORMAL again.

The VLM's verdict never diagnoses a medical condition: it only picks one of
three coarse classifications, and the human recipient acts on the SMS.
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time

from framework import events, transport

log = logging.getLogger("qonclave.hub")

# Generous cap: the model writes multi-sentence observations, and JSON cut
# off mid-object parses as nothing at all (-> a spurious UNCERTAIN).
INVESTIGATE_MAX_NEW_TOKENS = 384

INVESTIGATE_PROMPT_TEMPLATE = (
    "You are assisting a home-safety monitor. A fall-detection system flagged "
    "{identity} ({posture_context}). The image shows the scene now"
    "{history_note}.\n"
    "You cannot and must not diagnose any medical condition; only assess how "
    "the scene looks. Respond with ONLY a JSON object, no other text, of "
    "exactly this form:\n"
    '{{"classification": "EMERGENCY_LIKELY" or "SAFE_LIKELY" or "UNCERTAIN", '
    '"confidence": a number from 0 to 1, '
    '"observations": ["at most 3 observations, each under 10 words"], '
    '"recommended_action": "one short sentence for the human operator"}}\n'
    "Be terse: every observation a short phrase, not a sentence. "
    "Use EMERGENCY_LIKELY only if the person appears collapsed, unresponsive, "
    "or in visible distress; SAFE_LIKELY if they appear to be resting, "
    "exercising, or otherwise fine; UNCERTAIN if you cannot tell."
)

MANUAL_INVESTIGATE_PROMPT = (
    "You are assisting a home-safety monitor. A human operator asked for an "
    "on-demand check of this camera scene right now{history_note}.\n"
    "You cannot and must not diagnose any medical condition; only describe "
    "and assess how the scene looks. Respond with ONLY a JSON object, no "
    "other text, of exactly this form:\n"
    '{{"classification": "EMERGENCY_LIKELY" or "SAFE_LIKELY" or "UNCERTAIN", '
    '"confidence": a number from 0 to 1, '
    '"observations": ["at most 3 observations, each under 10 words"], '
    '"recommended_action": "one short sentence for the human operator"}}\n'
    "Be terse: every observation a short phrase, not a sentence. "
    "Use EMERGENCY_LIKELY only if someone appears collapsed, unresponsive, or "
    "in visible distress; SAFE_LIKELY if the scene appears fine (including an "
    "empty room); UNCERTAIN if you cannot tell."
)

CLASSIFICATIONS = ("EMERGENCY_LIKELY", "SAFE_LIKELY", "UNCERTAIN")

# Investigation state machine states.
MONITORING = "MONITORING"
WAITING_FOR_CAPTURE = "WAITING_FOR_CAPTURE"
VLM_RUNNING = "VLM_RUNNING"
COOLDOWN = "COOLDOWN"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def compose_investigation_image(main_jpeg: bytes, history: list) -> bytes:
    """Stack the investigation frame above a strip of the frozen evidence
    crops (oldest -> newest) so the single-image VLM sees the recent history
    too. Best-effort: any failure returns the main frame unchanged."""
    if not history:
        return main_jpeg
    try:
        import cv2
        import numpy as np

        def decode(data):
            return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

        main = decode(main_jpeg)
        if main is None:
            return main_jpeg
        thumbs = [t for t in (decode(j) for _ts, j in history[-5:]) if t is not None]
        if not thumbs:
            return main_jpeg

        strip_h = max(120, main.shape[0] // 4)
        resized = [
            cv2.resize(t, (max(1, round(t.shape[1] * strip_h / t.shape[0])), strip_h))
            for t in thumbs
        ]
        strip = cv2.hconcat(resized)
        width = max(main.shape[1], strip.shape[1])

        def pad(img):
            if img.shape[1] >= width:
                return img
            return cv2.copyMakeBorder(img, 0, 0, 0, width - img.shape[1],
                                      cv2.BORDER_CONSTANT, value=(0, 0, 0))

        strip = pad(strip)
        cv2.putText(strip, "history: oldest -> newest", (6, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        combined = cv2.vconcat([pad(main), strip])
        ok, encoded = cv2.imencode(".jpg", combined, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return encoded.tobytes() if ok else main_jpeg
    except Exception:
        log.exception("failed to compose investigation image; using main frame only")
        return main_jpeg


class InvestigationManager:
    """One active investigation at a time, driven by posture samples."""

    def __init__(self, vlm, sms, mqtt, clock=None, spawn_threads: bool = True):
        self.vlm = vlm
        self.sms = sms
        self.mqtt = mqtt
        self._clock = clock or time.monotonic
        # Tests drive the machine synchronously with a fake clock; production
        # uses real timers/threads so HTTP callers never wait on the VLM.
        self._spawn_threads = spawn_threads

        self.trigger_persistence_seconds = _env_float(
            "QONCLAVE_INVESTIGATION_PERSISTENCE_SEC", 5.0)
        self.capture_timeout_seconds = _env_float(
            "QONCLAVE_INVESTIGATION_CAPTURE_TIMEOUT_SEC", 10.0)
        self.cooldown_seconds = _env_float(
            "QONCLAVE_INVESTIGATION_COOLDOWN_SEC", 25.0)
        self.buffer_size = int(_env_float("QONCLAVE_INVESTIGATION_BUFFER_SIZE", 10))
        self.buffer_spacing_seconds = _env_float(
            "QONCLAVE_INVESTIGATION_BUFFER_SPACING_SEC", 1.0)

        self._lock = threading.Lock()
        self.state = MONITORING
        self._event_counter = 0
        self.active_event: dict | None = None
        self.vlm_in_progress = False
        self._cooldown_until = 0.0
        self._capture_deadline = 0.0
        self._capture_timer: threading.Timer | None = None
        self.last_result: dict | None = None
        self.vlm_calls = 0
        # Rolling evidence: per posture state key, deque of (ts, jpeg) kept at
        # ~1 fps regardless of the ~4 Hz pose sampling rate.
        self._buffers: dict = {}

    # --- posture feed -------------------------------------------------------

    def observe(self, track_id: int, image_bytes: bytes, analysis: dict | None):
        """Called for every posture sample of a known person. Buffers the
        crop, opens an investigation when the posture machine's own abnormal
        timer confirms a persistent SUSPICIOUS/DANGER state, and resolves
        COOLDOWN once the person is NORMAL again. Returns a small status dict
        for the /track/analyze response, or None when idle."""
        if not analysis:
            return None
        now = self._clock()
        identity = analysis.get("identity") or "Unknown"
        key = identity.casefold()
        state = analysis.get("state")

        run_vlm_args = None
        with self._lock:
            buf = self._buffers.setdefault(
                key, collections.deque(maxlen=self.buffer_size))
            if not buf or (now - buf[-1][0]) >= self.buffer_spacing_seconds:
                buf.append((now, image_bytes))

            if self.state == COOLDOWN:
                if now >= self._cooldown_until and state == "NORMAL":
                    log.info("Investigation cooldown over and %s back to NORMAL "
                             "-> MONITORING", identity)
                    self.state = MONITORING
                    self.active_event = None
                return self._status_locked()

            if self.state != MONITORING or self.active_event is not None:
                # An investigation is already active for this or another
                # person; never open a second one concurrently.
                if self.state == WAITING_FOR_CAPTURE and now >= self._capture_deadline:
                    run_vlm_args = self._capture_timed_out_locked()
                if run_vlm_args is None:
                    return self._status_locked()

            elif (state in ("SUSPICIOUS", "DANGER")
                    and (analysis.get("abnormal_duration_seconds") or 0.0)
                    >= self.trigger_persistence_seconds):
                self._open_event_locked(track_id, identity, key, state,
                                        analysis, image_bytes, now)
                return self._status_locked()
            else:
                return None

        # Fallthrough only from the timed-out-capture branch above. Runs off
        # the lock; _start_vlm keeps the caller (an HTTP request thread in
        # production) from blocking on the VLM itself.
        if run_vlm_args is not None:
            self._start_vlm(*run_vlm_args)
        return self.snapshot()

    # --- event lifecycle ----------------------------------------------------

    def _open_event_locked(self, track_id, identity, key, state, analysis,
                           danger_frame, now):
        self._event_counter += 1
        event_id = f"event_{self._event_counter:03d}"
        reason = ("dangerous_posture_and_low_motion" if state == "DANGER"
                  else "suspicious_posture")
        # Freeze the rolling buffer for this event; later samples keep
        # appending to the live buffer without touching the evidence.
        history = list(self._buffers.get(key, ()))
        event = {
            "event_id": event_id,
            "identity": identity,
            "track_id": track_id,
            "reason": reason,
            "posture_state": state,
            "analysis": dict(analysis),
            "history": history,
            "danger_frame": danger_frame,
            "opened_at": now,
        }
        self.active_event = event
        self.state = WAITING_FOR_CAPTURE
        self._capture_deadline = now + self.capture_timeout_seconds
        log.info("Investigation %s opened: %s for %s (track %s), state=%s, "
                 "%d buffered frames", event_id, reason, identity, track_id, state,
                 len(history))

        device_id = events.latest_device_id()
        command = {
            "type": "capture_investigation_image",
            "command": "capture_investigation_image",
            "event_id": event_id,
            "track_id": track_id,
        }
        published = bool(device_id) and self.mqtt.publish_command(device_id, command)
        if not published:
            # No device to ask for a better image -- fall back to the best
            # buffered frame immediately instead of waiting out the timeout.
            log.warning("Investigation %s: capture command not published "
                        "(device_id=%s) -> using buffered frame", event_id, device_id)
            self._capture_deadline = now  # next observe()/timer fires fallback
        if self._spawn_threads:
            delay = max(self._capture_deadline - now, 0.0)
            self._capture_timer = threading.Timer(delay, self.check_capture_timeout)
            self._capture_timer.daemon = True
            self._capture_timer.start()

    def trigger_manual(self, source: str = "dashboard",
                       notify_recipient: str | None = None) -> dict:
        """Open an operator-requested investigation (dashboard button or an
        inbound "CAPTURE" SMS): ask the edge for a fresh frame, run the VLM
        once, and present the result. Manual checks are allowed during
        COOLDOWN (the operator asked explicitly) but never while another
        investigation is mid-flight, and they resume the interrupted cooldown
        instead of clearing it. notify_recipient, when set, receives the VLM
        reasoning by SMS once the check finishes."""
        with self._lock:
            if self.state in (WAITING_FOR_CAPTURE, VLM_RUNNING):
                active = self.active_event
                return {"ok": False,
                        "error": "an investigation is already in progress",
                        "active_event_id": active and active["event_id"],
                        "state": self.state}

            now = self._clock()
            self._event_counter += 1
            event_id = f"event_{self._event_counter:03d}"
            # Newest buffered person crop (any identity) doubles as the
            # fallback frame if the edge capture never arrives.
            newest = None
            for buf in self._buffers.values():
                if buf and (newest is None or buf[-1][0] > newest[0]):
                    newest = buf[-1]
            event = {
                "event_id": event_id,
                "identity": "scene",
                "track_id": None,
                "reason": f"manual_request_{source}",
                "posture_state": "MANUAL",
                "analysis": {},
                "history": [],
                "danger_frame": newest[1] if newest else None,
                "opened_at": now,
                "manual": True,
                "source": source,
                "notify_recipient": notify_recipient,
                "resume_cooldown": self.state == COOLDOWN,
            }
            self.active_event = event
            self.state = WAITING_FOR_CAPTURE
            self._capture_deadline = now + self.capture_timeout_seconds
            log.info("Manual investigation %s opened (source=%s, notify=%s)",
                     event_id, source, notify_recipient or "none")

            device_id = events.latest_device_id()
            command = {
                "type": "capture_investigation_image",
                "command": "capture_investigation_image",
                "event_id": event_id,
                "track_id": None,
            }
            published = bool(device_id) and self.mqtt.publish_command(device_id, command)
            if not published:
                log.warning("Manual investigation %s: capture command not "
                            "published (device_id=%s) -> using latest buffered "
                            "frame", event_id, device_id)
                self._capture_deadline = now
            if self._spawn_threads:
                delay = max(self._capture_deadline - now, 0.0)
                self._capture_timer = threading.Timer(delay, self.check_capture_timeout)
                self._capture_timer.daemon = True
                self._capture_timer.start()
            return {"ok": True, "event_id": event_id, "state": self.state,
                    "capture_requested": published}

    def on_capture(self, event_id: str, image_bytes: bytes) -> dict:
        """The edge answered the capture command with a fresh frame."""
        with self._lock:
            event = self.active_event
            if (not event or event["event_id"] != event_id
                    or self.state != WAITING_FOR_CAPTURE):
                log.info("Ignoring investigation capture for %s "
                         "(state=%s, active=%s)", event_id, self.state,
                         event and event["event_id"])
                return {"ok": False, "error": "no matching investigation waiting"}
            if self._capture_timer is not None:
                self._capture_timer.cancel()
                self._capture_timer = None
            self.state = VLM_RUNNING
            self.vlm_in_progress = True
        log.info("Investigation %s: capture_complete received", event_id)
        self._start_vlm(event, image_bytes, "edge_capture")
        return {"ok": True, "event_id": event_id}

    def check_capture_timeout(self):
        """Give up on the edge capture and use the best buffered frame.
        Called by the capture timer (production) or directly (tests)."""
        with self._lock:
            args = self._capture_timed_out_locked()
        if args is not None:
            self._run_vlm(*args)

    def _capture_timed_out_locked(self):
        event = self.active_event
        if (not event or self.state != WAITING_FOR_CAPTURE
                or self._clock() < self._capture_deadline):
            return None
        if self._capture_timer is not None:
            self._capture_timer.cancel()
            self._capture_timer = None
        self.state = VLM_RUNNING
        self.vlm_in_progress = True
        best = event["danger_frame"] or (event["history"][-1][1]
                                         if event["history"] else None)
        log.warning("Investigation %s: capture timed out -> best buffered frame",
                    event["event_id"])
        return (event, best, "buffered_frame")

    # --- VLM ----------------------------------------------------------------

    def _start_vlm(self, event, image_bytes, source):
        if self._spawn_threads:
            threading.Thread(
                target=self._run_vlm, args=(event, image_bytes, source),
                name=f"Investigation-{event['event_id']}", daemon=True,
            ).start()
        else:
            self._run_vlm(event, image_bytes, source)

    def _prompt_for(self, event) -> str:
        history_note = (
            "; a strip of earlier snapshots (oldest to newest) is attached below it"
            if event.get("history") else ""
        )
        if event.get("manual"):
            return MANUAL_INVESTIGATE_PROMPT.format(history_note=history_note)
        analysis = event.get("analysis") or {}
        breakdown = analysis.get("score_breakdown") or {}
        cues = [name.replace("_", " ") for name, pts in breakdown.items() if pts and pts > 0]
        posture_context = (
            f"posture state {event['posture_state']}, "
            f"abnormal for {analysis.get('abnormal_duration_seconds', 0)}s, "
            f"motionless for {analysis.get('duration_seconds', 0)}s, "
            f"torso angle {analysis.get('torso_angle', '?')} deg from vertical"
        )
        if cues:
            posture_context += f"; cues: {', '.join(cues)}"
        return INVESTIGATE_PROMPT_TEMPLATE.format(
            identity=event["identity"],
            posture_context=posture_context,
            history_note=history_note,
        )

    def _run_vlm(self, event, image_bytes, source):
        event_id = event["event_id"]
        result = {"available": False, "parsed": {}, "error": "no frame available"}
        frame_name = None
        if image_bytes:
            composite = compose_investigation_image(image_bytes, event.get("history") or [])
            frame_name = f"investigation_{event_id}.jpg"
            path = os.path.join(transport.UPLOAD_DIR, frame_name)
            try:
                with open(path, "wb") as f:
                    f.write(composite)
            except OSError:
                log.exception("Investigation %s: failed to save frame", event_id)
                frame_name = None
            if frame_name:
                self.vlm_calls += 1
                result = self.vlm.structured_query(
                    path, self._prompt_for(event), INVESTIGATE_MAX_NEW_TOKENS,
                    json_mode=True, temperature=0.1,
                )

        self._finish(event, result, source, frame_name)

    # --- result handling ----------------------------------------------------

    def _finish(self, event, result, source, frame_name):
        event_id = event["event_id"]
        parsed = result.get("parsed") or {}
        classification = str(parsed.get("classification") or "").strip().upper()
        if classification not in CLASSIFICATIONS:
            # Unparseable output, VLM unavailable, or no frame at all: a human
            # must look -- never silently drop a triggered investigation.
            classification = "UNCERTAIN"
        try:
            confidence = float(parsed.get("confidence"))
        except (TypeError, ValueError):
            confidence = None
        observations = parsed.get("observations")
        if not isinstance(observations, list):
            observations = [str(observations)] if observations else []
        recommended = str(parsed.get("recommended_action") or "").strip()

        identity = event["identity"]
        message = None
        recipient = events.latest_device_id() or "unknown"
        if event.get("manual"):
            # Operator-requested check: the reply IS the VLM reasoning, sent
            # back to whoever asked (SMS trigger); dashboard triggers just
            # present the result there.
            recipient = event.get("notify_recipient")
            if recipient:
                summary = "; ".join(observations) if observations else "no details"
                message = (f"VLM check [{classification}"
                           + (f" {confidence:.2f}" if confidence is not None else "")
                           + f"]: {summary}."
                           + (f" {recommended}" if recommended else ""))
                if not result.get("available") and not frame_name:
                    message = ("VLM check failed: no camera image could be "
                               "captured. Please check the camera feed directly.")
        elif classification == "EMERGENCY_LIKELY":
            message = (f"EMERGENCY: {identity} may need help "
                       f"({event['reason'].replace('_', ' ')}). "
                       f"{recommended or 'Please check on them immediately.'}")
        elif classification == "UNCERTAIN":
            message = (f"Please check on {identity}: prolonged "
                       f"{event['posture_state'].lower()} posture detected and "
                       "automatic verification was inconclusive. "
                       f"{recommended}").strip()
        sms_sent = False
        if message is not None and self.sms is not None:
            from framework.policy import Notification
            sms_sent = self.sms.send(Notification(message=message, recipient=recipient))

        now = self._clock()
        outcome = {
            "event_id": event_id,
            "identity": identity,
            "track_id": event["track_id"],
            "reason": event["reason"],
            "posture_state": event["posture_state"],
            "classification": classification,
            "confidence": confidence,
            "observations": observations,
            "recommended_action": recommended,
            "image_source": source,
            "frame": frame_name,
            "vlm_available": result.get("available"),
            "vlm_error": result.get("error"),
            "latency_s": result.get("latency_s"),
            "sms_sent": sms_sent,
            "sms_message": message if sms_sent else None,
            "manual": bool(event.get("manual")),
            "source": event.get("source"),
            "finished_at": transport.now_iso(),
        }
        log.info("Investigation %s finished: %s (conf=%s, source=%s, sms=%s)",
                 event_id, classification, confidence, source, sms_sent)

        # Surface the investigation on the dashboard's event feed.
        events.record_event({
            "schema_version": events.SCHEMA_VERSION,
            "event_id": event_id,
            "event_type": "investigation",
            "received": True,
            "hub_verified": classification != "SAFE_LIKELY",
            "hub_confidence": confidence,
            "alert": (f"Manual check {classification}"
                      f" ({event['reason'].replace('_', ' ')})"
                      if event.get("manual") else
                      f"Investigation {classification}: {identity} "
                      f"({event['reason'].replace('_', ' ')})"),
            "reasoning_text": "; ".join(observations) +
                              (f" -> {recommended}" if recommended else ""),
            "reasoning_available": bool(result.get("available")),
            "latency_s": result.get("latency_s"),
            "device_id": events.latest_device_id(),
            "frame": frame_name,
            "received_at": transport.now_iso(),
        }, frame_name)

        with self._lock:
            self.last_result = outcome
            self.vlm_in_progress = False
            if event.get("manual"):
                # An operator check doesn't start a cooldown of its own; it
                # resumes an interrupted one (so an automatic event that just
                # finished still needs its NORMAL sighting) or goes straight
                # back to monitoring.
                self.state = COOLDOWN if event.get("resume_cooldown") else MONITORING
                self.active_event = None
            else:
                self.state = COOLDOWN
                self._cooldown_until = now + self.cooldown_seconds
                # The event stays "active" through cooldown so a lingering
                # DANGER posture can't open a duplicate; observe() clears it
                # once the cooldown has elapsed AND the person has been seen
                # NORMAL.

    # --- introspection ------------------------------------------------------

    def _status_locked(self) -> dict:
        event = self.active_event
        return {
            "state": self.state,
            "active_event_id": event["event_id"] if event else None,
            "event_target_identity": event["identity"] if event else None,
            "vlm_in_progress": self.vlm_in_progress,
        }

    def snapshot(self) -> dict:
        with self._lock:
            status = self._status_locked()
            status.update({
                "vlm_calls": self.vlm_calls,
                "cooldown_seconds_left": max(self._cooldown_until - self._clock(), 0.0)
                                          if self.state == COOLDOWN else 0.0,
                "trigger_persistence_seconds": self.trigger_persistence_seconds,
                "capture_timeout_seconds": self.capture_timeout_seconds,
                "cooldown_seconds": self.cooldown_seconds,
                "last_result": self.last_result,
            })
            return status
