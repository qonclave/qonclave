# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
Tests for python/led_display.py -- run directly with `python
test_led_display.py`, or with pytest if it happens to be installed.
Follows this app's existing convention for standalone test scripts (see
test_edge_mqtt_e2e.py, test_person_tracker.py): plain assert-based
test_*() functions, no test framework added to requirements.txt.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from led_display import person_position_bitmap, GRID_COLS, GRID_ROWS  # noqa: E402

FRAME_W = 640
FRAME_H = 480


def _lit_cells(bitmap):
    return {(r, c) for r, row in enumerate(bitmap) for c, val in enumerate(row) if val}


def _assert_shape(bitmap):
    assert len(bitmap) == GRID_ROWS
    for row in bitmap:
        assert len(row) == GRID_COLS
        for val in row:
            assert val in (0, 1)


def test_center_maps_near_grid_center():
    bitmap = person_position_bitmap((FRAME_W / 2, FRAME_H / 2), FRAME_W, FRAME_H)
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    assert lit
    for r, c in lit:
        assert abs(r - GRID_ROWS / 2) <= 2
        assert abs(c - GRID_COLS / 2) <= 2


def test_top_left_corner_maps_in_bounds():
    bitmap = person_position_bitmap((0, 0), FRAME_W, FRAME_H)
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    assert lit
    for r, c in lit:
        assert r <= 1
        assert c <= 1


def test_top_right_corner_maps_in_bounds():
    bitmap = person_position_bitmap((FRAME_W, 0), FRAME_W, FRAME_H)
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    assert lit
    for r, c in lit:
        assert r <= 1
        assert c >= GRID_COLS - 2


def test_bottom_left_corner_maps_in_bounds():
    bitmap = person_position_bitmap((0, FRAME_H), FRAME_W, FRAME_H)
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    assert lit
    for r, c in lit:
        assert r >= GRID_ROWS - 2
        assert c <= 1


def test_bottom_right_corner_maps_in_bounds():
    bitmap = person_position_bitmap((FRAME_W, FRAME_H), FRAME_W, FRAME_H)
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    assert lit
    for r, c in lit:
        assert r >= GRID_ROWS - 2
        assert c >= GRID_COLS - 2


def test_output_shape_always_8x12():
    for centroid in [(0, 0), (FRAME_W, FRAME_H), (FRAME_W / 2, FRAME_H / 2), (-10, -10), (FRAME_W + 50, FRAME_H + 50)]:
        _assert_shape(person_position_bitmap(centroid, FRAME_W, FRAME_H))


def run_all():
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
