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
from led_display import (  # noqa: E402
    person_position_bitmap,
    emotion_bitmap,
    person_display_bitmap,
    GRID_COLS,
    GRID_ROWS,
)

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


def _is_ring_cell(r, c):
    return r in (0, GRID_ROWS - 1) or c in (0, GRID_COLS - 1)


def test_position_never_lights_interior_cell():
    centroids = [
        (FRAME_W / 2, FRAME_H / 2),
        (0, 0),
        (FRAME_W, 0),
        (0, FRAME_H),
        (FRAME_W, FRAME_H),
        (FRAME_W / 2, 0),
        (FRAME_W / 2, FRAME_H),
        (0, FRAME_H / 2),
        (FRAME_W, FRAME_H / 2),
        (-100, -100),
        (FRAME_W + 200, FRAME_H + 200),
    ]
    for centroid in centroids:
        bitmap = person_position_bitmap(centroid, FRAME_W, FRAME_H)
        _assert_shape(bitmap)
        for r, c in _lit_cells(bitmap):
            assert _is_ring_cell(r, c), f"centroid {centroid} lit interior cell ({r},{c})"


def test_straight_above_center_maps_to_top_row():
    bitmap = person_position_bitmap((FRAME_W / 2, 0), FRAME_W, FRAME_H)
    lit = _lit_cells(bitmap)
    assert lit
    assert all(r == 0 for r, c in lit)


def test_straight_below_center_maps_to_bottom_row():
    bitmap = person_position_bitmap((FRAME_W / 2, FRAME_H), FRAME_W, FRAME_H)
    lit = _lit_cells(bitmap)
    assert lit
    assert all(r == GRID_ROWS - 1 for r, c in lit)


def test_straight_left_of_center_maps_to_left_col():
    bitmap = person_position_bitmap((0, FRAME_H / 2), FRAME_W, FRAME_H)
    lit = _lit_cells(bitmap)
    assert lit
    assert all(c == 0 for r, c in lit)


def test_straight_right_of_center_maps_to_right_col():
    bitmap = person_position_bitmap((FRAME_W, FRAME_H / 2), FRAME_W, FRAME_H)
    lit = _lit_cells(bitmap)
    assert lit
    assert all(c == GRID_COLS - 1 for r, c in lit)


def test_dead_center_does_not_raise_and_lands_on_ring():
    bitmap = person_position_bitmap((FRAME_W / 2, FRAME_H / 2), FRAME_W, FRAME_H)
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    assert lit
    for r, c in lit:
        assert _is_ring_cell(r, c)


def test_emotion_bitmap_only_lights_interior():
    bitmap = emotion_bitmap("smiley")
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    assert lit
    for r, c in lit:
        assert not _is_ring_cell(r, c)


def test_person_display_bitmap_composes_position_and_emotion():
    bitmap = person_display_bitmap((FRAME_W / 2, 0), FRAME_W, FRAME_H)
    _assert_shape(bitmap)
    lit = _lit_cells(bitmap)
    ring_lit = {(r, c) for r, c in lit if _is_ring_cell(r, c)}
    interior_lit = {(r, c) for r, c in lit if not _is_ring_cell(r, c)}
    assert ring_lit, "position indicator missing from composed bitmap"
    assert interior_lit, "emotion icon missing from composed bitmap"
    assert interior_lit == _lit_cells(emotion_bitmap("smiley"))


def test_output_shape_always_8x12():
    for centroid in [(0, 0), (FRAME_W, FRAME_H), (FRAME_W / 2, FRAME_H / 2), (-10, -10), (FRAME_W + 50, FRAME_H + 50)]:
        _assert_shape(person_position_bitmap(centroid, FRAME_W, FRAME_H))
        _assert_shape(person_display_bitmap(centroid, FRAME_W, FRAME_H))


def run_all():
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
