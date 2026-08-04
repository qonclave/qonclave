# SPDX-License-Identifier: MPL-2.0

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from person_centering import (  # noqa: E402
    PersonCenteringController,
    horizontal_bearing_degrees,
)


def test_normal_camera_center_is_zero():
    assert horizontal_bearing_degrees((320, 240), 640, 480) == 0.0


def test_normal_camera_edges_match_half_fov():
    assert math.isclose(horizontal_bearing_degrees((0, 240), 640, 480), -35.0)
    assert math.isclose(horizontal_bearing_degrees((640, 240), 640, 480), 35.0)


def test_stacked_front_and_rear_lens_bearings():
    kwargs = {"dual_lens_stacked": True, "dual_lens_fov_degrees": 180.0}
    assert horizontal_bearing_degrees((320, 360), 640, 480, **kwargs) == 0.0
    assert horizontal_bearing_degrees((320, 120), 640, 480, **kwargs) == -180.0
    assert horizontal_bearing_degrees((640, 360), 640, 480, **kwargs) == 90.0
    assert horizontal_bearing_degrees((0, 360), 640, 480, **kwargs) == -90.0


def test_controller_uses_exact_error_direction_and_rounded_magnitude():
    controller = PersonCenteringController(minimum_interval_seconds=0)
    right = controller.command_for(23.6, track_id=7, now=0)
    assert right.direction == "RIGHT"
    assert right.magnitude == 24
    assert right.angle_error_degrees == 23.6

    controller = PersonCenteringController(minimum_interval_seconds=0)
    left = controller.command_for(-12.2, track_id=8, now=0)
    assert left.direction == "LEFT"
    assert left.magnitude == 12


def test_controller_tolerance_cap_and_cooldown():
    controller = PersonCenteringController(
        tolerance_degrees=3, max_turn_degrees=45, minimum_interval_seconds=1
    )
    assert controller.command_for(2.9, track_id=1, now=0) is None
    first = controller.command_for(120, track_id=1, now=0)
    assert first.magnitude == 45
    assert controller.command_for(30, track_id=1, now=0.5) is None
    assert controller.command_for(30, track_id=1, now=1.0) is not None


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
