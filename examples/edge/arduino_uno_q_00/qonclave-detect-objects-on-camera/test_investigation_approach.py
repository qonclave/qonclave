# SPDX-License-Identifier: MPL-2.0

"""Tests for the pre-capture approach plan.

The behaviour that matters: the robot gets closer before the VLM sees the
scene, it never spends longer doing so than the hub will wait, and it never
turns on a bearing it cannot trust.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from investigation_approach import (  # noqa: E402
    ApproachStep,
    describe,
    plan_approach,
)


def test_centered_target_just_drives_forward():
    steps = plan_approach(1.0, forward_seconds=1)
    assert [s.direction for s in steps] == ["FORWARD"]
    assert steps[0].magnitude == 1  # seconds, the MCU's unit for FORWARD


def test_off_center_target_is_faced_first_then_approached():
    steps = plan_approach(30.0, forward_seconds=1)
    assert [s.direction for s in steps] == ["RIGHT", "FORWARD"]
    # Full correction, unlike the damped centering loop: this is a one-shot
    # alignment before a photo, so undershooting only costs image quality.
    assert steps[0].magnitude == 30

    steps = plan_approach(-30.0, forward_seconds=1)
    assert [s.direction for s in steps] == ["LEFT", "FORWARD"]
    assert steps[0].magnitude == 30


def test_turn_is_capped_so_the_budget_survives():
    steps = plan_approach(170.0, forward_seconds=1, max_turn_degrees=45)
    assert steps[0].direction == "RIGHT"
    assert steps[0].magnitude == 45
    assert steps[1].direction == "FORWARD"  # the approach still happens


def test_unknown_bearing_skips_the_turn_but_still_approaches():
    # No recent bearing means we cannot aim, but the centering loop has been
    # keeping the target roughly ahead -- forward is still the best guess.
    steps = plan_approach(None, forward_seconds=1)
    assert [s.direction for s in steps] == ["FORWARD"]


def test_a_tight_budget_drops_steps_rather_than_overrunning():
    # The hub abandons the capture after its timeout and uses a buffered crop,
    # so an approach that overruns throws away the frame it exists to get.
    steps = plan_approach(40.0, forward_seconds=1, settle_seconds=0.6,
                         budget_seconds=1.2)
    # Only the turn fits (40 deg * 12ms = 0.48s, +0.6s settle = 1.08s of the
    # 1.2s budget); forward needs 1.0s + settle and is dropped, not shortened.
    assert [s.direction for s in steps] == ["RIGHT"]

    assert plan_approach(40.0, forward_seconds=1, budget_seconds=0.0) == []


def test_magnitudes_stay_inside_the_mcu_accepted_range():
    # main.py rejects anything outside 1..360 before the Bridge call, so a
    # plan that produced 0 or 400 would silently become a no-op approach.
    for bearing in (0.4, -0.4, 9.0, 400.0, -400.0):
        for step in plan_approach(bearing, forward_seconds=1,
                                  max_turn_degrees=400):
            assert 1 <= step.magnitude <= 360, (bearing, step)


def test_tolerance_means_small_errors_do_not_earn_a_turn():
    steps = plan_approach(7.0, forward_seconds=1, tolerance_degrees=8)
    assert [s.direction for s in steps] == ["FORWARD"]
    steps = plan_approach(9.0, forward_seconds=1, tolerance_degrees=8)
    assert [s.direction for s in steps] == ["RIGHT", "FORWARD"]


def test_forward_can_be_disabled_leaving_only_alignment():
    steps = plan_approach(30.0, forward_seconds=0)
    assert [s.direction for s in steps] == ["RIGHT"]


def test_describe_is_readable_and_units_are_labelled():
    text = describe(plan_approach(30.0, forward_seconds=1))
    assert "RIGHT 30deg" in text
    assert "FORWARD 1s" in text
    assert describe([]) == "no approach (nothing fits the budget)"


def test_steps_are_hashable_value_objects():
    # frozen dataclass: a plan can be logged/compared without defensive copies
    assert ApproachStep("FORWARD", 1, 1.0, "x") == ApproachStep("FORWARD", 1, 1.0, "x")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
