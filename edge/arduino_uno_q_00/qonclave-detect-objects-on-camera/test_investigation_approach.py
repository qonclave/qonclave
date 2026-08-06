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
    steps = plan_approach(1.0, forward_ms=1000)
    assert [s.direction for s in steps] == ["FORWARD"]
    assert steps[0].magnitude == 1000  # ms, the MCU's unit for FORWARD


def test_off_center_target_is_faced_first_then_approached():
    steps = plan_approach(30.0, forward_ms=1000)
    assert [s.direction for s in steps] == ["RIGHT", "FORWARD"]
    # Full correction, unlike the damped centering loop: this is a one-shot
    # alignment before a photo, so undershooting only costs image quality.
    assert steps[0].magnitude == 30

    steps = plan_approach(-30.0, forward_ms=1000)
    assert [s.direction for s in steps] == ["LEFT", "FORWARD"]
    assert steps[0].magnitude == 30


def test_turn_is_capped_so_the_budget_survives():
    steps = plan_approach(170.0, forward_ms=1000, max_turn_degrees=45)
    assert steps[0].direction == "RIGHT"
    assert steps[0].magnitude == 45
    assert steps[1].direction == "FORWARD"  # the approach still happens


def test_unknown_bearing_skips_the_turn_but_still_approaches():
    # No recent bearing means we cannot aim, but the centering loop has been
    # keeping the target roughly ahead -- forward is still the best guess.
    steps = plan_approach(None, forward_ms=1000)
    assert [s.direction for s in steps] == ["FORWARD"]


def test_already_close_person_is_not_approached_further():
    # The hub's approach flag may close in a LITTLE past the everyday safe
    # distance, but max_size_ratio is the line: a person already filling that
    # much of the frame gains nothing from another step, and box size is all
    # that stands between "close-up" and "contact" -- no proximity sensing.
    steps = plan_approach(1.0, size_ratio=0.80, max_size_ratio=0.75,
                          forward_ms=1000)
    assert steps == []
    # Exactly at the cap counts as there: don't go any further.
    steps = plan_approach(1.0, size_ratio=0.75, max_size_ratio=0.75,
                          forward_ms=1000)
    assert steps == []


def test_close_but_under_the_cap_still_gets_its_little_step():
    # Inside the follow band's "too close" zone (>0.65) but under the
    # investigation cap: one bounded step closer is allowed for the photo.
    steps = plan_approach(1.0, size_ratio=0.70, max_size_ratio=0.75,
                          forward_ms=1000)
    assert [s.direction for s in steps] == ["FORWARD"]


def test_already_close_person_is_still_faced():
    # The size cap drops only the forward step; a capture of the wrong wall
    # helps no one, so the alignment turn survives.
    steps = plan_approach(30.0, size_ratio=0.90, max_size_ratio=0.75,
                          forward_ms=1000)
    assert [s.direction for s in steps] == ["RIGHT"]


def test_unknown_size_keeps_the_forward_step():
    # No recent measurement: the everyday distance keeper has been holding
    # the safe band, so one bounded step from inside it is safe.
    steps = plan_approach(1.0, size_ratio=None, forward_ms=1000)
    assert [s.direction for s in steps] == ["FORWARD"]


def test_a_tight_budget_drops_steps_rather_than_overrunning():
    # The hub abandons the capture after its timeout and uses a buffered crop,
    # so an approach that overruns throws away the frame it exists to get.
    steps = plan_approach(40.0, forward_ms=1000, settle_seconds=0.6,
                         budget_seconds=1.2)
    # Only the turn fits (40 deg * 12ms = 0.48s, +0.6s settle = 1.08s of the
    # 1.2s budget); forward needs 1.0s + settle and is dropped, not shortened.
    assert [s.direction for s in steps] == ["RIGHT"]

    assert plan_approach(40.0, forward_ms=1000, budget_seconds=0.0) == []


def test_magnitudes_stay_inside_the_mcu_accepted_range():
    # main.py rejects turn magnitudes outside 1..360 before the Bridge call,
    # so a plan that produced 0 or 400 would silently become a no-op turn.
    for bearing in (0.4, -0.4, 9.0, 400.0, -400.0):
        for step in plan_approach(bearing, forward_ms=1000,
                                  max_turn_degrees=400):
            if step.direction in ("LEFT", "RIGHT"):
                assert 1 <= step.magnitude <= 360, (bearing, step)
            else:
                assert 1 <= step.magnitude <= 5000, (bearing, step)


def test_tolerance_means_small_errors_do_not_earn_a_turn():
    steps = plan_approach(7.0, forward_ms=1000, tolerance_degrees=8)
    assert [s.direction for s in steps] == ["FORWARD"]
    steps = plan_approach(9.0, forward_ms=1000, tolerance_degrees=8)
    assert [s.direction for s in steps] == ["RIGHT", "FORWARD"]


def test_forward_can_be_disabled_leaving_only_alignment():
    steps = plan_approach(30.0, forward_ms=0)
    assert [s.direction for s in steps] == ["RIGHT"]


def test_describe_is_readable_and_units_are_labelled():
    text = describe(plan_approach(30.0, forward_ms=1000))
    assert "RIGHT 30deg" in text
    assert "FORWARD 1000ms" in text
    assert describe([]).startswith("no approach")


def test_steps_are_hashable_value_objects():
    # frozen dataclass: a plan can be logged/compared without defensive copies
    assert ApproachStep("FORWARD", 1, 1.0, "x") == ApproachStep("FORWARD", 1, 1.0, "x")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
