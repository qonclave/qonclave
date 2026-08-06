# SPDX-License-Identifier: MPL-2.0

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from track_overlay import draw_track_overlay  # noqa: E402


def _make_frame_jpeg(width=200, height=200):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (120, 140, 160)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    return encoded.tobytes()


def _decode(jpeg_bytes):
    return cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)


def test_no_tracks_returns_valid_unchanged_looking_frame():
    frame = _make_frame_jpeg()
    out = draw_track_overlay(frame, [], {})
    decoded = _decode(out)
    assert decoded is not None
    assert decoded.shape[:2] == (200, 200)


def test_box_and_label_are_drawn_for_a_track():
    frame = _make_frame_jpeg()
    tracks = [{"track_id": 4, "bounding_box_xyxy": (20, 20, 100, 120)}]
    plain = _decode(frame)
    out = draw_track_overlay(frame, tracks, {4: "Track 4: Jogendra"})
    annotated = _decode(out)
    assert annotated is not None
    assert annotated.shape == plain.shape
    # Something changed inside the box region -- the overlay was actually drawn.
    assert not np.array_equal(plain[15:30, 15:110], annotated[15:30, 15:110])


def test_track_missing_from_labels_still_gets_a_box_with_fallback_text():
    frame = _make_frame_jpeg()
    tracks = [{"track_id": 9, "bounding_box_xyxy": (10, 10, 60, 60)}]
    plain = _decode(frame)
    out = draw_track_overlay(frame, tracks, {})  # no label provided for track 9
    annotated = _decode(out)
    assert not np.array_equal(plain, annotated)


def test_multiple_tracks_each_get_a_box():
    frame = _make_frame_jpeg()
    tracks = [
        {"track_id": 1, "bounding_box_xyxy": (5, 5, 40, 40)},
        {"track_id": 2, "bounding_box_xyxy": (120, 120, 180, 180)},
    ]
    plain = _decode(frame)
    out = draw_track_overlay(frame, tracks, {1: "Track 1: Unknown", 2: "Track 2: Unknown"})
    annotated = _decode(out)
    assert not np.array_equal(plain[0:45, 0:45], annotated[0:45, 0:45])
    assert not np.array_equal(plain[115:185, 115:185], annotated[115:185, 115:185])


def test_highlighted_track_is_drawn_differently():
    frame = _make_frame_jpeg()
    tracks = [{"track_id": 4, "bounding_box_xyxy": (20, 20, 100, 120)}]
    plain = _decode(draw_track_overlay(frame, tracks, {}))
    highlighted = _decode(draw_track_overlay(frame, tracks, {}, highlight_track_id=4))
    # Same geometry, different color: the box region must differ.
    assert not np.array_equal(plain, highlighted)
    # Highlighting a non-existent id changes nothing vs. the default drawing.
    other = _decode(draw_track_overlay(frame, tracks, {}, highlight_track_id=99))
    assert np.array_equal(plain, other)


def test_undecodable_frame_returns_input_unchanged():
    garbage = b"not a jpeg"
    out = draw_track_overlay(garbage, [{"track_id": 1, "bounding_box_xyxy": (0, 0, 10, 10)}], {})
    assert out == garbage


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
