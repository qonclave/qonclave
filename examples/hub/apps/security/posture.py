"""Security-app posture features and temporal NORMAL/SUSPICIOUS/DANGER state."""

from __future__ import annotations

import math
import struct
import threading
import time
from dataclasses import asdict, dataclass


@dataclass
class PostureSettings:
    """Deliberately SENSITIVE thresholds.

    This stage is a screening filter for a medical emergency, not the
    decision. Every threshold below is set on the permissive side of the
    measured normal/collapsed clusters, and several cues award partial
    credit for a merely *suspicious* reading. A false positive costs one
    VLM call (and, if the VLM agrees something looks wrong, one text a
    human can ignore); a false negative costs someone having a heart
    attack unattended. The VLM investigation is the layer that says no.
    """

    # 2s (down from 3): the alert path adds its own persistence check, so
    # the posture machine should hand a candidate over quickly.
    abnormal_seconds: float = 2.0
    # 3s (down from 5/7): someone genuinely motionless for three seconds
    # while showing any postural cue is worth a look.
    motionless_seconds: float = 3.0
    # 0.045 (up from 0.03/0.025): errs toward calling movement "still".
    # Measured live: a motionless slump blipped to 0.025 on sensor noise,
    # real fidgeting measures 0.04+, and small settling motions in a
    # collapse read in between -- those now count as still.
    movement_threshold: float = 0.045
    # 35 deg (down from 45/55): acted collapses measured 50-55 deg, normal
    # upright sitting stays under ~25. 35 sits just above the normal
    # cluster rather than midway, so a partial slump still registers.
    horizontal_angle: float = 35.0
    # Any lean past this earns PARTIAL credit (see score_parts): it is not
    # alarming alone, but combined with stillness it is enough.
    partial_angle: float = 20.0
    # Hip/shoulder height and box-aspect gates, all relaxed: a collapse in
    # a chair keeps the hips near seat height and the box taller than wide,
    # so these cues rarely fire at all -- loose values let them contribute.
    low_hip_ratio: float = 0.55
    low_shoulder_ratio: float = 0.45
    wide_box_ratio: float = 0.75
    rapid_drop_ratio_per_second: float = 0.15
    # 0.2 (down from 0.25): a lower bar for "this keypoint is usable" means
    # fewer samples are discarded outright. A discarded sample is a blind
    # frame, and blind frames are how a collapse goes unnoticed.
    keypoint_threshold: float = 0.2
    # A gap this long (person left and came back, hub restarted, etc.) means
    # the next sample starts a fresh session instead of resuming old timers.
    # Must comfortably exceed normal pose-sampling gaps (~0.25s at 4 Hz) so
    # jitter/network hiccups never trigger it.
    session_gap_seconds: float = 30.0
    # How long the abnormal/stillness timers survive samples that briefly
    # contradict them. One noisy frame at ~4 Hz (a keypoint flicker breaking
    # the tilt/head cue, a movement blip just over threshold) must not zero a
    # timer for a person who has been down for minutes -- a missed collapse
    # is unrecoverable, a slightly early alert is filtered by the VLM.
    # Genuine recovery (several seconds upright/moving) still resets.
    blip_grace_seconds: float = 3.0


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Return JPEG (width, height) without an imaging dependency."""
    i = 2
    if data[:2] != b"\xff\xd8":
        return None
    while i + 8 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        i += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(data):
            break
        length = struct.unpack(">H", data[i:i + 2])[0]
        if marker in range(0xC0, 0xD4) and marker not in (0xC4, 0xC8, 0xCC):
            if i + 7 <= len(data):
                height, width = struct.unpack(">HH", data[i + 3:i + 7])
                return width, height
        i += max(length, 2)
    return None


def _center(points, a, b, threshold):
    valid = [points[i] for i in (a, b) if points[i][2] >= threshold]
    if not valid:
        return None
    return (sum(p[0] for p in valid) / len(valid),
            sum(p[1] for p in valid) / len(valid))


def _held_timer(active, now, since, last_active, grace_seconds):
    """A ``since`` timestamp that survives brief contradicting samples.

    While ``active``, the timer keeps (or starts) ``since`` and stamps
    ``last_active``. When inactive, ``since`` is held as long as the last
    active sample is within ``grace_seconds`` -- so a single noisy frame
    can't zero a timer that a real collapse has been accumulating -- and
    only a sustained recovery clears it."""
    if active:
        return (since if since is not None else now), now
    if (since is not None and last_active is not None
            and now - last_active <= grace_seconds):
        return since, last_active
    return None, None


def _joint_angle(a, b, c):
    ba, bc = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    denom = math.hypot(*ba) * math.hypot(*bc)
    if not denom:
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (ba[0] * bc[0] + ba[1] * bc[1]) / denom))))


class PostureStateMachine:
    """One lock-guarded temporal state per edge track id."""

    def __init__(self, settings=None, clock=time.monotonic):
        self.settings = settings or PostureSettings()
        self._clock = clock
        self._tracks = {}
        self._track_keys = {}
        self._lock = threading.Lock()

    def settings_dict(self):
        with self._lock:
            return asdict(self.settings)

    def update_settings(self, values):
        allowed = asdict(self.settings)
        parsed = {}
        for key, value in values.items():
            if key not in allowed:
                continue
            number = float(value)
            if number <= 0:
                raise ValueError(f"{key} must be greater than zero")
            parsed[key] = number
        with self._lock:
            for key, value in parsed.items():
                setattr(self.settings, key, value)
            return asdict(self.settings)

    def analyze(self, track_id, image_bytes, face, pose):
        if not pose or pose.get("status") != "ok" or not pose.get("keypoints"):
            return None
        size = jpeg_size(image_bytes)
        if not size:
            return None
        now = self._clock()
        points = pose["keypoints"]
        if len(points) != 17:
            return None
        with self._lock:
            return self._analyze_locked(track_id, size, points, face, now)

    def _analyze_locked(self, track_id, size, points, face, now):
        cfg = self.settings
        width, height = size
        shoulder = _center(points, 5, 6, cfg.keypoint_threshold)
        hip = _center(points, 11, 12, cfg.keypoint_threshold)
        valid = [(i, p) for i, p in enumerate(points) if p[2] >= cfg.keypoint_threshold]
        if shoulder is None or hip is None or len(valid) < 4:
            return None

        dx, dy = hip[0] - shoulder[0], hip[1] - shoulder[1]
        torso_angle = math.degrees(math.atan2(abs(dx), abs(dy)))
        # A FORWARD slump (head hanging on the chest) keeps the shoulder->hip
        # line vertical in the image, so torso_angle stays ~0 and no other
        # rule can see it (measured live: acted collapse showed torso 0-9 deg
        # while clearly slumped). The head gives it away instead: the nose /
        # eyes at or below the shoulder line never happens sitting upright.
        #
        # head_sinking is the sensitive half of the same cue -- the head only
        # PART of the way down, which is what the first seconds of a slump
        # look like and what a partly-occluded head reads as. It earns
        # partial credit rather than being ignored until fully dropped.
        head_pts = [points[i] for i in (0, 1, 2)
                    if points[i][2] >= cfg.keypoint_threshold]
        head_y = (sum(p[1] for p in head_pts) / len(head_pts)) if head_pts else None
        torso_len = max(abs(hip[1] - shoulder[1]), 1.0)
        head_dropped = head_y is not None and head_y >= shoulder[1]
        # 0.15 of torso length above the shoulder line: an upright head sits
        # ~0.3-0.4 up there, so this fires once the head has fallen roughly
        # halfway. Looser than this (0.35 was tried) and an ordinary standing
        # person reads as sinking, which alerts constantly and drowns the VLM.
        head_sinking = (head_y is not None
                        and head_y >= shoulder[1] - 0.15 * torso_len)
        xs, ys = [p[0] for _, p in valid], [p[1] for _, p in valid]
        box_w, box_h = max(xs) - min(xs), max(ys) - min(ys)
        aspect = box_w / max(box_h, 1.0)
        hip_ratio, shoulder_ratio = hip[1] / height, shoulder[1] / height

        # A detector can assign a new track ID during a fall or when a looped
        # video jumps back to its first frame. Known people have a stable key,
        # so their abnormal/motionless timers survive that tracker churn.
        identity = (face.get("identity")
                    if face and face.get("status") == "known" else None)
        state_key = (("identity", identity.casefold()) if identity
                     else self._track_keys.get(track_id, ("track", track_id)))
        self._track_keys[track_id] = state_key
        old = self._tracks.get(state_key, {})
        # A gap this long means the person left and came back (or the hub
        # restarted) since the last sample under this key -- resume as a
        # fresh session instead of reactivating a stale abnormal/still timer
        # that could be hours old.
        if now - old.get("ts", now) > cfg.session_gap_seconds:
            old = {}
        elapsed = max(now - old.get("ts", now), 1e-6)
        previous = old.get("points") or {}

        # Compare body-relative coordinates. Raw pose coordinates belong to a
        # crop that is re-sized/re-positioned every frame; comparing them
        # directly makes a motionless person look active when the crop changes.
        norm_w, norm_h = max(box_w, 1.0), max(box_h, 1.0)
        normalized = {
            i: ((p[0] - min(xs)) / norm_w, (p[1] - min(ys)) / norm_h)
            for i, p in valid
        }
        distances = [math.hypot(p[0] - previous[i][0], p[1] - previous[i][1])
                     for i, p in normalized.items() if i in previous]
        movement = (sum(distances) / len(distances)) if distances else 0.0
        rapid_drop = bool(old.get("hip_ratio") is not None and
                          (hip_ratio - old["hip_ratio"]) / elapsed >= cfg.rapid_drop_ratio_per_second)
        low_movement = movement <= cfg.movement_threshold and bool(previous)

        knee_angles = []
        for a, b, c in ((11, 13, 15), (12, 14, 16)):
            if all(points[i][2] >= cfg.keypoint_threshold for i in (a, b, c)):
                knee_angles.append(_joint_angle(points[a], points[b], points[c]))
        knees_bent = bool(knee_angles and min(knee_angles) < 145.0)
        # A forward slump looks exactly like normal sitting EXCEPT for the
        # hanging head, so the seated exemption must not apply then -- and it
        # only takes the head STARTING to sink to withdraw the exemption.
        seated = (torso_angle < 30.0 and hip_ratio >= 0.52 and knees_bent
                  and aspect < 0.90 and not head_sinking)

        # Graded cues: a full-strength reading scores 3, a merely suspicious
        # one scores 2. Partial credit is the point -- a collapse rarely
        # presents a textbook signature at this camera angle, so a couple of
        # half-convincing cues must be able to reach the alert threshold
        # instead of each being rounded down to "nothing seen".
        score_parts = {
            "torso_horizontal": (3 if torso_angle >= cfg.horizontal_angle
                                 else 2 if torso_angle >= cfg.partial_angle else 0),
            "head_below_shoulders": (3 if head_dropped
                                     else 2 if head_sinking else 0),
            "body_low": 2 if hip_ratio >= cfg.low_hip_ratio and shoulder_ratio >= cfg.low_shoulder_ratio else 0,
            "box_wider_than_tall": 2 if aspect >= cfg.wide_box_ratio else 0,
            "rapid_downward_transition": 3 if rapid_drop else 0,
            "minimal_movement": 0,
            # -1, not -2: the seated exemption exists to stop a desk chair
            # reading as a collapse, but a heart attack HAPPENS in chairs.
            # It nudges the score down; it can no longer veto an alert.
            "normal_seated_torso": -1 if seated else 0,
        }
        base_score = sum(score_parts.values())
        # Arming the timer is cheap -- it costs nothing until the score also
        # crosses the alert threshold -- so the bar is low on purpose: ANY
        # single postural cue, or any partial cue at all while the person is
        # motionless. Three separately acted chair collapses showed no
        # reliable tilt or head reading at this camera angle (torso 0-11 deg,
        # head above the shoulder line); the only signature present every
        # time was a still body holding an unremarkable-looking pose. Under a
        # stricter gate that case never arms at all, and never alerts.
        abnormal = base_score >= 2 or (base_score >= 1 and low_movement)
        abnormal_since, last_abnormal = _held_timer(
            abnormal, now, old.get("abnormal_since"), old.get("last_abnormal"),
            cfg.blip_grace_seconds)
        still_since, last_still = _held_timer(
            low_movement, now, old.get("still_since"), old.get("last_still"),
            cfg.blip_grace_seconds)
        abnormal_duration = now - abnormal_since if abnormal_since is not None else 0.0
        still_duration = now - still_since if still_since is not None else 0.0
        if still_duration >= cfg.motionless_seconds:
            score_parts["minimal_movement"] = 3
        score = max(0, sum(score_parts.values()))

        # SUSPICIOUS at score >= 3 (was > 3): one full-strength cue, or two
        # partial ones, sustained for abnormal_seconds. SUSPICIOUS is not an
        # alarm -- it opens a VLM investigation, and that is exactly the
        # threshold at which a second opinion is worth its cost. DANGER at
        # >= 6 (was >= 7) so a two-cue collapse can still reach the top
        # state instead of capping at SUSPICIOUS.
        if abnormal_duration < cfg.abnormal_seconds or score < 3:
            state = "NORMAL"
        elif score >= 6 and still_duration >= cfg.motionless_seconds:
            state = "DANGER"
        else:
            state = "SUSPICIOUS"

        identity = identity or old.get("identity")
        self._tracks[state_key] = {
            "ts": now, "points": normalized, "hip_ratio": hip_ratio,
            "abnormal_since": abnormal_since, "last_abnormal": last_abnormal,
            "still_since": still_since, "last_still": last_still,
            "identity": identity,
        }
        return {
            "identity": identity or "Unknown", "state": state, "posture_score": score,
            "torso_angle": round(torso_angle, 1), "movement": "Low" if low_movement else "Active",
            "movement_ratio": round(movement, 4),
            # The held stillness duration, not a per-sample reading: a blip
            # that the grace window absorbs must not show 0s on the dashboard
            # while the state machine is still counting.
            "duration_seconds": round(still_duration, 1),
            "abnormal_duration_seconds": round(abnormal_duration, 1),
            "shoulder_center": [round(v, 1) for v in shoulder],
            "hip_center": [round(v, 1) for v in hip],
            "box_width_height_ratio": round(aspect, 2), "hip_height_ratio": round(hip_ratio, 2),
            "knees_bent": knees_bent, "normal_seated": seated,
            "head_dropped": head_dropped, "head_sinking": head_sinking,
            "score_breakdown": score_parts,
        }
