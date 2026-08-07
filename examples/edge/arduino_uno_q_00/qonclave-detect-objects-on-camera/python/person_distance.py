# SPDX-License-Identifier: MPL-2.0

"""
person_distance.py -- keep the followed person at a safe, useful distance.

The centering controller (person_centering.py) rotates the robot so the
followed person stays in the middle of the frame; this controller adds the
missing axis: drive FORWARD when they are too far to see clearly, BACK away
when they are uncomfortably close, and hold inside a deadband between the two.

Distance is inferred from the person's bounding-box size, as the fraction of
the frame their box fills:

    size_ratio = max(box_w / frame_w, box_h / frame_h)

The MAX of the two dimensions -- not height -- because box shape depends on
posture. An upright person is tall (height dominates); a FALLEN person is wide
and short, and by height alone they would read as "far away", sending the
robot driving into someone lying on the floor. Whatever their posture, the
larger dimension keeps growing as the robot closes in, so it is the one safe
proxy for distance.

Stability, in a deliberately conservative order:
  * a deadband (approach_below .. retreat_above) with a wide gap, so there is
    no ratio at which the robot oscillates between forward and backward;
  * a confirmation streak: the same verdict must hold for consecutive frames
    before a move is issued -- one flickering detection box must not lurch a
    robot that is pointed at a person;
  * post-motion blanking, same rationale as centering: boxes measured from
    frames captured while the robot was moving describe a distance it no
    longer has, and keep arriving for ~1 pipeline latency after it stops;
  * one small timed step per decision (the MCU's FORWARD/BACKWARD unit is
    whole milliseconds), then re-measure -- never a proportional "drive
    until correct" motion toward a human being.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class DistanceCommand:
    direction: str          # FORWARD (approach) or BACKWARD (retreat)
    magnitude: int          # milliseconds, the MCU's unit for these directions
    size_ratio: float       # the measurement the decision was made on
    track_id: int
    reason: str


def size_ratio_of(box_w: float, box_h: float,
                  frame_w: float, frame_h: float) -> float | None:
    """Fraction of the frame the person fills, posture-independent (see
    module docstring). None when a degenerate box/frame makes it undefined."""
    if box_w <= 0 or box_h <= 0 or frame_w <= 0 or frame_h <= 0:
        return None
    return max(box_w / frame_w, box_h / frame_h)


class PersonDistanceController:
    """Converts person box sizes into paced FORWARD/BACKWARD nudges."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        approach_below: float = 0.35,
        retreat_above: float = 0.65,
        step_ms: int = 500,
        minimum_interval_seconds: float = 2.5,
        confirm_frames: int = 3,
        post_motion_blank_seconds: float = 1.5,
    ):
        self.enabled = enabled
        self.approach_below = max(0.01, approach_below)
        # The gap between the thresholds is the hold zone; never let a config
        # invert them into an always-moving band.
        self.retreat_above = max(self.approach_below + 0.05, retreat_above)
        self.step_ms = max(1, int(step_ms))  # MCU minimum is 1ms
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self.confirm_frames = max(1, int(confirm_frames))
        self.post_motion_blank_seconds = max(0.0, post_motion_blank_seconds)
        self._blank_until = 0.0
        self._next_command_at = 0.0
        self._streak_direction: str | None = None
        self._streak_track: int | None = None
        self._streak = 0

    def zone_for(self, size_ratio: float | None) -> str:
        """'approach' / 'hold' / 'retreat' for a measured ratio (UI/status)."""
        if size_ratio is None:
            return "hold"
        if size_ratio < self.approach_below:
            return "approach"
        if size_ratio > self.retreat_above:
            return "retreat"
        return "hold"

    def note_motion(
        self, duration_seconds: float = 0.0, now: float | None = None
    ) -> None:
        """Record robot motion (any source); boxes are stale until
        post_motion_blank_seconds after it ends. Also drops the confirmation
        streak: pre-move and post-move measurements must not add up."""
        now = time.monotonic() if now is None else now
        self._blank_until = max(
            self._blank_until,
            now + max(0.0, duration_seconds) + self.post_motion_blank_seconds,
        )
        self._reset_streak()

    def command_for(
        self,
        box_w: float,
        box_h: float,
        frame_w: float,
        frame_h: float,
        track_id: int,
        now: float | None = None,
    ) -> DistanceCommand | None:
        if not self.enabled:
            return None

        now = time.monotonic() if now is None else now
        if now < self._blank_until:
            return None

        ratio = size_ratio_of(box_w, box_h, frame_w, frame_h)
        zone = self.zone_for(ratio)
        if ratio is None or zone == "hold":
            self._reset_streak()
            return None

        direction = "FORWARD" if zone == "approach" else "BACKWARD"

        # The streak is per verdict AND per person: a target switch mid-streak
        # means the frames counted so far measured someone else.
        if direction != self._streak_direction or track_id != self._streak_track:
            self._streak_direction = direction
            self._streak_track = track_id
            self._streak = 0
        self._streak += 1
        if self._streak < self.confirm_frames:
            return None

        if now < self._next_command_at:
            return None

        self._next_command_at = now + max(
            self.minimum_interval_seconds, self.step_ms / 1000.0
        )
        # Streak resets after every issued step so the NEXT step needs fresh
        # confirmation from post-move frames too.
        self._reset_streak()
        return DistanceCommand(
            direction=direction,
            magnitude=self.step_ms,
            size_ratio=round(ratio, 3),
            track_id=track_id,
            reason=("person too small in frame -> approach"
                    if direction == "FORWARD"
                    else "person too large in frame -> back away"),
        )

    def _reset_streak(self) -> None:
        self._streak_direction = None
        self._streak_track = None
        self._streak = 0
