# SPDX-License-Identifier: MPL-2.0

"""Tests for distance keeping (person_distance.py).

The behaviours that matter: the robot closes in on a person too small to see,
backs off from one too close, holds inside the deadband, never lurches on a
single flickering box, and never misreads a FALLEN person's wide flat box as
"far away" -- that misread would drive the robot into someone on the floor.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from person_distance import (  # noqa: E402
    PersonDistanceController,
    size_ratio_of,
)

FRAME_W, FRAME_H = 640, 480


def controller(**overrides):
    defaults = dict(minimum_interval_seconds=0.0, confirm_frames=1,
                    post_motion_blank_seconds=0.0)
    defaults.update(overrides)
    return PersonDistanceController(**defaults)


# --- the size metric ---------------------------------------------------------

def test_upright_person_size_comes_from_height():
    # 100x360 box in 640x480: height dominates.
    assert size_ratio_of(100, 360, FRAME_W, FRAME_H) == 360 / 480


def test_fallen_person_size_comes_from_width():
    # A person lying across the frame: 500 wide, 120 tall. By height alone
    # (0.25) they would read as beyond approach_below -> approach. The max
    # dimension (500/640 = 0.78) reads them as CLOSE, which they are.
    assert size_ratio_of(500, 120, FRAME_W, FRAME_H) == 500 / 640


def test_degenerate_boxes_have_no_ratio():
    assert size_ratio_of(0, 100, FRAME_W, FRAME_H) is None
    assert size_ratio_of(100, -5, FRAME_W, FRAME_H) is None
    assert size_ratio_of(100, 100, 0, FRAME_H) is None


# --- zone decisions ----------------------------------------------------------

def test_small_person_approaches():
    c = controller()
    move = c.command_for(60, 120, FRAME_W, FRAME_H, track_id=4, now=0.0)
    assert move.direction == "FORWARD"
    assert move.magnitude == 1
    assert move.track_id == 4


def test_large_person_retreats():
    c = controller()
    move = c.command_for(300, 460, FRAME_W, FRAME_H, track_id=4, now=0.0)
    assert move.direction == "BACKWARD"


def test_fallen_person_close_up_is_never_approached():
    # The regression this module exists to prevent: wide flat box, height
    # ratio 0.25 ("far" by height), width ratio 0.78 (actually close).
    c = controller()
    move = c.command_for(500, 120, FRAME_W, FRAME_H, track_id=4, now=0.0)
    assert move is None or move.direction != "FORWARD"
    assert move.direction == "BACKWARD"


def test_comfortable_distance_holds():
    c = controller()
    # Height ratio 0.5: inside the 0.35..0.65 deadband.
    assert c.command_for(120, 240, FRAME_W, FRAME_H, track_id=4, now=0.0) is None


def test_zone_labels_match_decisions():
    c = controller()
    assert c.zone_for(0.2) == "approach"
    assert c.zone_for(0.5) == "hold"
    assert c.zone_for(0.8) == "retreat"
    assert c.zone_for(None) == "hold"


def test_disabled_controller_never_moves():
    c = controller(enabled=False)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, track_id=4, now=0.0) is None


def test_inverted_thresholds_are_repaired_not_obeyed():
    # A config with retreat below approach would make every ratio command a
    # move in one direction or the other, with no hold zone at all.
    c = controller(approach_below=0.6, retreat_above=0.4)
    assert c.retreat_above > c.approach_below


# --- flicker debounce --------------------------------------------------------

def test_single_flicker_frame_does_not_move_the_robot():
    c = controller(confirm_frames=3)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.0) is None
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.7) is None
    move = c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=1.4)
    assert move.direction == "FORWARD"


def test_streak_resets_when_the_verdict_changes():
    c = controller(confirm_frames=2)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.0) is None  # far
    assert c.command_for(120, 240, FRAME_W, FRAME_H, 4, now=0.7) is None  # hold
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=1.4) is None  # far #1 again
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=2.1) is not None


def test_streak_resets_when_the_target_changes():
    # Two frames measuring DIFFERENT people must not add up to one decision.
    c = controller(confirm_frames=2)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, track_id=4, now=0.0) is None
    assert c.command_for(60, 120, FRAME_W, FRAME_H, track_id=9, now=0.7) is None
    assert c.command_for(60, 120, FRAME_W, FRAME_H, track_id=9, now=1.4) is not None


# --- pacing + blanking -------------------------------------------------------

def test_moves_are_paced_not_continuous():
    c = controller(minimum_interval_seconds=2.5)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.0) is not None
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=1.0) is None
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=3.0) is not None


def test_each_step_needs_fresh_confirmation():
    # After a nudge, the next one must be re-confirmed from post-move frames,
    # not carried over from the pre-move streak.
    c = controller(confirm_frames=2, minimum_interval_seconds=0.0)
    c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.0)
    c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.7)  # issues (streak 2)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=1.4) is None  # streak 1
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=2.1) is not None


def test_boxes_measured_during_motion_are_discarded():
    c = controller(post_motion_blank_seconds=1.5)
    c.note_motion(duration_seconds=1.0, now=0.0)  # blanked until 2.5
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=2.0) is None
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=2.6) is not None


def test_motion_drops_the_confirmation_streak():
    # Pre-move and post-move measurements must not add up to one decision.
    c = controller(confirm_frames=2, post_motion_blank_seconds=0.0)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.0) is None
    c.note_motion(now=0.1)
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.7) is None
    assert c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=1.4) is not None


def test_magnitude_is_a_valid_mcu_argument():
    # main.py rejects magnitudes outside 1..360 before the Bridge call; a 0
    # would silently turn distance keeping into a no-op.
    c = controller(step_seconds=0)
    move = c.command_for(60, 120, FRAME_W, FRAME_H, 4, now=0.0)
    assert isinstance(move.magnitude, int)
    assert 1 <= move.magnitude <= 360


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
