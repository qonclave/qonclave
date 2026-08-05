"""Security-app posture features and temporal NORMAL/SUSPICIOUS/DANGER state."""

from __future__ import annotations

import math
import struct
import threading
import time
from dataclasses import asdict, dataclass


@dataclass
class PostureSettings:
    abnormal_seconds: float = 3.0
    motionless_seconds: float = 7.0
    movement_threshold: float = 0.025
    horizontal_angle: float = 55.0
    low_hip_ratio: float = 0.62
    low_shoulder_ratio: float = 0.50
    wide_box_ratio: float = 0.90
    rapid_drop_ratio_per_second: float = 0.22
    keypoint_threshold: float = 0.25
    # A gap this long (person left and came back, hub restarted, etc.) means
    # the next sample starts a fresh session instead of resuming old timers.
    # Must comfortably exceed normal pose-sampling gaps (~0.25s at 4 Hz) so
    # jitter/network hiccups never trigger it.
    session_gap_seconds: float = 30.0


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
        seated = torso_angle < 30.0 and hip_ratio >= 0.52 and knees_bent and aspect < 0.90

        score_parts = {
            "torso_horizontal": 3 if torso_angle >= cfg.horizontal_angle else 0,
            "body_low": 2 if hip_ratio >= cfg.low_hip_ratio and shoulder_ratio >= cfg.low_shoulder_ratio else 0,
            "box_wider_than_tall": 2 if aspect >= cfg.wide_box_ratio else 0,
            "rapid_downward_transition": 3 if rapid_drop else 0,
            "minimal_movement": 0,
            "normal_seated_torso": -2 if seated else 0,
        }
        base_score = sum(score_parts.values())
        abnormal = base_score >= 4
        abnormal_since = old.get("abnormal_since") if abnormal and old.get("abnormal") else (now if abnormal else None)
        still_since = old.get("still_since") if low_movement and old.get("low_movement") else (now if low_movement else None)
        abnormal_duration = now - abnormal_since if abnormal_since is not None else 0.0
        still_duration = now - still_since if still_since is not None else 0.0
        if still_duration >= cfg.motionless_seconds:
            score_parts["minimal_movement"] = 3
        score = max(0, sum(score_parts.values()))

        if abnormal_duration < cfg.abnormal_seconds or score <= 3:
            state = "NORMAL"
        elif score >= 7 and still_duration >= cfg.motionless_seconds:
            state = "DANGER"
        else:
            state = "SUSPICIOUS"

        identity = identity or old.get("identity")
        self._tracks[state_key] = {
            "ts": now, "points": normalized, "hip_ratio": hip_ratio,
            "abnormal": abnormal, "abnormal_since": abnormal_since,
            "low_movement": low_movement, "still_since": still_since,
            "identity": identity,
        }
        unchanged = still_duration if low_movement else 0.0
        return {
            "identity": identity or "Unknown", "state": state, "posture_score": score,
            "torso_angle": round(torso_angle, 1), "movement": "Low" if low_movement else "Active",
            "movement_ratio": round(movement, 4), "duration_seconds": round(unchanged, 1),
            "abnormal_duration_seconds": round(abnormal_duration, 1),
            "shoulder_center": [round(v, 1) for v in shoulder],
            "hip_center": [round(v, 1) for v in hip],
            "box_width_height_ratio": round(aspect, 2), "hip_height_ratio": round(hip_ratio, 2),
            "knees_bent": knees_bent, "normal_seated": seated, "score_breakdown": score_parts,
        }
