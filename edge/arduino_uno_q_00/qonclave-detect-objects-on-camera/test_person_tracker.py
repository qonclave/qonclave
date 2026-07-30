# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
Tests for python/person_tracker.py -- run directly with `python
test_person_tracker.py`, or with pytest if it happens to be installed.
Follows this app's existing convention for standalone test scripts (see
test_edge_mqtt_e2e.py): plain assert-based test_*() functions, no test
framework added to requirements.txt.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from person_tracker import PersonTracker  # noqa: E402


def _box(cx, cy, w=20, h=40):
    """A bounding_box_xyxy centered at (cx, cy)."""
    return {"confidence": 0.9, "bounding_box_xyxy": (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)}


def test_single_person_keeps_same_id_across_frames():
    tracker = PersonTracker()

    r1 = tracker.update([_box(100, 100)])
    r2 = tracker.update([_box(105, 102)])
    r3 = tracker.update([_box(110, 104)])

    assert r1[0]["track_id"] == r2[0]["track_id"] == r3[0]["track_id"]
    assert r3[0]["frames_tracked"] == 3


def test_two_people_get_distinct_stable_ids():
    tracker = PersonTracker()

    r1 = tracker.update([_box(50, 50), _box(400, 400)])
    id_a, id_b = r1[0]["track_id"], r1[1]["track_id"]
    assert id_a != id_b

    # Both move a little, order in the list stays the same.
    r2 = tracker.update([_box(55, 52), _box(405, 402)])
    assert r2[0]["track_id"] == id_a
    assert r2[1]["track_id"] == id_b

    # Now they show up in reverse order in the detections list -- nearest
    # centroid matching should still assign the correct existing ID to each.
    r3 = tracker.update([_box(410, 404), _box(60, 54)])
    assert r3[0]["track_id"] == id_b
    assert r3[1]["track_id"] == id_a


def test_direction_labels():
    tracker = PersonTracker(direction_history=5, min_movement_px=10)

    # Move steadily right.
    for x in (100, 120, 140, 160, 180):
        result = tracker.update([_box(x, 100)])
    assert result[0]["direction"] == "right"

    tracker = PersonTracker(direction_history=5, min_movement_px=10)
    for y in (100, 120, 140, 160, 180):
        result = tracker.update([_box(100, y)])
    assert result[0]["direction"] == "down"

    tracker = PersonTracker(direction_history=5, min_movement_px=10)
    for x in (200, 180, 160, 140, 120):
        result = tracker.update([_box(x, 100)])
    assert result[0]["direction"] == "left"


def test_stationary_person_is_labeled_stationary():
    tracker = PersonTracker(direction_history=5, min_movement_px=10)

    result = None
    for _ in range(5):
        result = tracker.update([_box(100, 100)])
    assert result[0]["direction"] == "stationary"


def test_track_dropped_after_max_disappeared_then_new_id_assigned():
    tracker = PersonTracker(max_disappeared=3, max_distance=150)

    r1 = tracker.update([_box(100, 100)])
    original_id = r1[0]["track_id"]

    # Person leaves frame for longer than max_disappeared frames of *other*
    # activity (some other object still detected, so update() is invoked;
    # see the on_detect_all invocation-frequency note in the tracker module).
    for _ in range(4):
        tracker.update([])

    # New detection near the old location arrives after the track was
    # deregistered -- must get a fresh ID, not the deregistered one.
    r2 = tracker.update([_box(102, 101)])
    assert r2[0]["track_id"] != original_id


def test_track_survives_disappearance_within_window():
    tracker = PersonTracker(max_disappeared=3, max_distance=150)

    r1 = tracker.update([_box(100, 100)])
    original_id = r1[0]["track_id"]

    for _ in range(2):
        tracker.update([])

    r2 = tracker.update([_box(102, 101)])
    assert r2[0]["track_id"] == original_id


def test_far_detection_registers_as_new_track_not_merge():
    tracker = PersonTracker(max_distance=50)

    r1 = tracker.update([_box(100, 100)])
    original_id = r1[0]["track_id"]

    # 500px away, well beyond max_distance -- must not be matched to the
    # existing track.
    r2 = tracker.update([_box(600, 600)])
    assert r2[0]["track_id"] != original_id


def run_all():
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
