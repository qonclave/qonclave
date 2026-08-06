"""Plan the robot's short approach toward a person before an investigation
capture.

When posture monitoring on the hub goes SUSPICIOUS/DANGER it opens an
investigation and asks this device for one fresh frame. A frame shot from
across the room is the weakest evidence the VLM could be given -- a slumped
person 5 m away is a handful of pixels -- so the robot first closes some
distance: turn to face them, drive forward briefly, then capture.

The whole approach is bounded by a time budget, because the hub gives up on
the capture after ``capture_timeout_seconds`` (10 s by default) and falls
back to a buffered crop. Overrunning the budget therefore doesn't just delay
the alert, it throws away the better frame this module exists to get. Steps
that don't fit are dropped rather than truncated.

This module is pure planning -- no Bridge, MQTT or camera calls -- so the
decision is testable without hardware. main.py executes the returned steps.
"""

from __future__ import annotations

from dataclasses import dataclass

# The MCU clamps magnitude to this range (MotorController::move), and the
# Python dispatcher rejects anything outside it before the Bridge call.
MIN_MAGNITUDE = 1
MAX_MAGNITUDE = 360


@dataclass(frozen=True)
class ApproachStep:
    """One robot command in the approach.

    ``magnitude`` is degrees for LEFT/RIGHT and *seconds* for FORWARD -- the
    MCU's own convention (MotorController::move), kept rather than
    normalized so what is planned is literally what is commanded.
    """

    direction: str
    magnitude: int
    estimated_seconds: float
    reason: str


def plan_approach(
    bearing_degrees: float | None,
    *,
    size_ratio: float | None = None,
    max_size_ratio: float = 0.75,
    forward_seconds: int = 1,
    tolerance_degrees: float = 8.0,
    max_turn_degrees: float = 45.0,
    ms_per_degree: float = 12.0,
    settle_seconds: float = 0.6,
    budget_seconds: float = 6.0,
) -> list[ApproachStep]:
    """Return the steps to run before capturing, newest-bearing first.

    ``bearing_degrees`` is the signed horizontal bearing to the target
    (negative = left of center), or None when no recent bearing is available.

    ``size_ratio`` is how much of the frame the person already fills (the
    distance keeper's posture-independent metric, person_distance.py), or
    None when no recent measurement exists. The capture is allowed to get a
    LITTLE closer than the everyday safe-follow distance -- a close-up is
    exactly what the VLM needs -- but ``max_size_ratio`` is the line: a
    person already filling that much of the frame gains nothing from another
    forward step, and this robot has no proximity sensing, so the box size is
    the only thing standing between "close-up" and "contact". At or past the
    cap the forward step is dropped; the turn still happens.

    The turn corrects the FULL measured error, unlike the continuous
    centering controller's damped gain: this is a single one-shot alignment
    before a capture, not a loop that can oscillate, so undershooting just
    means a worse photo.

    With no bearing at all the turn is skipped but the forward step still
    runs -- the centering loop has been keeping the target roughly ahead, so
    forward remains the best available guess at "toward them". An unknown
    size keeps the forward step too: the everyday distance keeper has been
    holding the safe band, so one bounded step from inside it is safe.
    """
    steps: list[ApproachStep] = []
    remaining = max(0.0, budget_seconds)

    def fits(cost: float) -> bool:
        # Every step must leave room for the post-motion settle, or the frame
        # is captured mid-wobble and the approach buys a blurrier photo.
        return cost + settle_seconds <= remaining

    if bearing_degrees is not None and abs(bearing_degrees) > tolerance_degrees:
        degrees = min(abs(bearing_degrees), max_turn_degrees)
        magnitude = _clamp_magnitude(round(degrees))
        cost = magnitude * ms_per_degree / 1000.0
        if fits(cost):
            steps.append(ApproachStep(
                direction="RIGHT" if bearing_degrees > 0 else "LEFT",
                magnitude=magnitude,
                estimated_seconds=cost,
                reason=f"face target ({bearing_degrees:+.1f} deg off center)",
            ))
            remaining -= cost

    already_close = size_ratio is not None and size_ratio >= max_size_ratio
    if forward_seconds >= 1 and not already_close:
        magnitude = _clamp_magnitude(int(forward_seconds))
        cost = float(magnitude)  # FORWARD magnitude is seconds
        if fits(cost):
            steps.append(ApproachStep(
                direction="FORWARD",
                magnitude=magnitude,
                estimated_seconds=cost,
                reason=f"close distance for {magnitude}s before capture",
            ))

    return steps


def _clamp_magnitude(value: float) -> int:
    return max(MIN_MAGNITUDE, min(MAX_MAGNITUDE, int(value)))


def describe(steps: list[ApproachStep]) -> str:
    """One-line log summary of a plan."""
    if not steps:
        return "no approach (already close enough, or nothing fits the budget)"
    return ", ".join(
        f"{s.direction} {s.magnitude}"
        f"{'deg' if s.direction in ('LEFT', 'RIGHT') else 's'} ({s.reason})"
        for s in steps
    )
