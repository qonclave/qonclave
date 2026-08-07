# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""Camera bearing estimation and turn-command pacing for person centering."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


def _wrap_degrees(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def horizontal_bearing_degrees(
    centroid: tuple[float, float],
    frame_width: int,
    frame_height: int,
    *,
    horizontal_fov_degrees: float = 70.0,
    dual_lens_stacked: bool = False,
    dual_lens_fov_degrees: float = 180.0,
) -> float:
    """Return signed bearing from the robot/camera center to a person.

    Negative angles are left and positive angles are right. Normal cameras
    use a pinhole projection. For the configured stacked 360 camera, the
    bottom image is the front lens and the top image is the rear lens; each
    lens uses an equidistant fisheye approximation across its width.
    """
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("Frame dimensions must be positive")

    cx, cy = centroid
    normalized_x = max(-1.0, min(1.0, (2.0 * cx / frame_width) - 1.0))

    if dual_lens_stacked:
        lens_offset = normalized_x * (dual_lens_fov_degrees / 2.0)
        lens_center = 180.0 if cy < frame_height / 2.0 else 0.0
        return _wrap_degrees(lens_center + lens_offset)

    if not 0.0 < horizontal_fov_degrees < 180.0:
        raise ValueError("Horizontal field of view must be between 0 and 180 degrees")
    focal_length_px = frame_width / (
        2.0 * math.tan(math.radians(horizontal_fov_degrees) / 2.0)
    )
    return math.degrees(math.atan((cx - frame_width / 2.0) / focal_length_px))


@dataclass(frozen=True)
class TurnCommand:
    direction: str
    magnitude: int
    angle_error_degrees: float
    track_id: int


class PersonCenteringController:
    """Converts tracked-person bearings into paced LEFT/RIGHT commands.

    Stability measures against the detection pipeline's latency (~1s from
    frame capture to bearing on this board):

    - Post-motion blanking: bearings measured from frames captured while the
      robot was moving describe a view the robot has already rotated away
      from, and they keep arriving for roughly one pipeline latency after the
      motion ends. ``note_motion()`` records commanded/observed motion, and
      ``command_for`` discards every bearing until
      ``post_motion_blank_seconds`` after the motion is expected to finish --
      acting on those bearings is what sustains the turn -> shifted box ->
      counter-turn ping-pong.
    - Turn gain: each turn corrects only ``turn_gain`` of the measured error,
      so a stale or noisy bearing produces an undershoot that the next fresh
      bearing finishes off, instead of an overshoot that reverses direction.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        tolerance_degrees: float = 3.0,
        max_turn_degrees: float = 90.0,
        minimum_interval_seconds: float = 0.75,
        estimated_ms_per_degree: float = 12.0,
        settle_seconds: float = 0.35,
        post_motion_blank_seconds: float = 1.5,
        turn_gain: float = 0.7,
    ):
        self.enabled = enabled
        self.tolerance_degrees = max(0.0, tolerance_degrees)
        self.max_turn_degrees = max(1.0, max_turn_degrees)
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self.estimated_ms_per_degree = max(0.0, estimated_ms_per_degree)
        self.settle_seconds = max(0.0, settle_seconds)
        self.post_motion_blank_seconds = max(0.0, post_motion_blank_seconds)
        self.turn_gain = min(1.0, max(0.1, turn_gain))
        self._next_command_at = 0.0
        self._blank_until = 0.0

    def note_motion(
        self, duration_seconds: float = 0.0, now: float | None = None
    ) -> None:
        """Record robot motion starting now and lasting ``duration_seconds``.

        Call this when a move command is issued (any source: auto-centering,
        web UI, hub MQTT) with the move's estimated duration, and with the
        default duration whenever the MCU reports motion in progress. Bearings
        are then discarded until ``post_motion_blank_seconds`` after the
        motion ends, which must cover the detection pipeline latency so no
        frame captured mid-motion can still command a turn.
        """
        now = time.monotonic() if now is None else now
        self._blank_until = max(
            self._blank_until,
            now + max(0.0, duration_seconds) + self.post_motion_blank_seconds,
        )

    def command_for(
        self, angle_error_degrees: float, track_id: int, now: float | None = None
    ) -> TurnCommand | None:
        if not self.enabled:
            return None

        now = time.monotonic() if now is None else now
        if now < self._blank_until:
            # This bearing came from (or cannot be distinguished from) a frame
            # captured while the camera itself was moving.
            return None

        if abs(angle_error_degrees) <= self.tolerance_degrees:
            return None

        if now < self._next_command_at:
            return None

        correction = min(
            abs(angle_error_degrees) * self.turn_gain, self.max_turn_degrees
        )
        magnitude = max(1, int(round(correction)))
        duration = magnitude * self.estimated_ms_per_degree / 1000.0
        self._next_command_at = now + max(
            self.minimum_interval_seconds, duration + self.settle_seconds
        )
        return TurnCommand(
            direction="RIGHT" if angle_error_degrees > 0 else "LEFT",
            magnitude=magnitude,
            angle_error_degrees=angle_error_degrees,
            track_id=track_id,
        )
