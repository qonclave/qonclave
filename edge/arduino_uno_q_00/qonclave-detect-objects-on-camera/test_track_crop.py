# SPDX-License-Identifier: MPL-2.0

import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from track_crop import crop_person, remove_crop_locally, save_crop_locally  # noqa: E402


def _make_frame_jpeg(width=200, height=200):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (120, 140, 160)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    return encoded.tobytes()


def test_normal_box_is_cropped_with_padding_and_clamped():
    frame = _make_frame_jpeg(200, 200)
    crop = crop_person(frame, (50, 50, 150, 150), padding=0.25, padding_top=0.25, min_size_px=40)
    assert crop is not None
    decoded = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = decoded.shape[:2]
    # box=100x100, padding=0.25 on every side -> +25px each -> 150x150, fully in-frame.
    assert (w, h) == (150, 150)


def test_padding_is_clamped_to_frame_bounds():
    frame = _make_frame_jpeg(200, 200)
    # Box touches the top-left corner; padding would go negative without clamping.
    crop = crop_person(frame, (0, 0, 60, 60), padding=0.5, padding_top=0.5, min_size_px=40)
    assert crop is not None
    decoded = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = decoded.shape[:2]
    assert w <= 200 and h <= 200
    assert w >= 40 and h >= 40


def test_top_gets_more_padding_than_bottom_by_default():
    frame = _make_frame_jpeg(200, 200)
    # Box centered with room to spare on every side: padding_top (default 0.8)
    # should push the top edge up further than padding (default 0.25) pushes
    # the bottom edge down.
    crop = crop_person(frame, (80, 80, 120, 120))  # 40x40 box, defaults
    assert crop is not None
    decoded = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = decoded.shape[:2]
    # top: 80 - 40*0.8 = 48 -> crop starts at y=48 (32px above the box)
    # bottom: 120 + 40*0.25 = 130 -> crop ends at y=130 (10px below the box)
    assert h == 130 - 48
    assert w == (120 + 40 * 0.25) - (80 - 40 * 0.25)


def test_generous_top_padding_uses_available_headroom_near_frame_edge():
    frame = _make_frame_jpeg(200, 200)
    # Box top has 50px of real headroom before the frame edge. The old
    # symmetric 0.25 padding (25px) left most of that headroom unused, which
    # is exactly the real-world bug this asymmetry fixes: a face sitting near
    # the top of a person's box getting clipped instead of given margin.
    crop = crop_person(frame, (50, 50, 150, 150), padding=0.25, padding_top=0.6)
    assert crop is not None
    decoded = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, _ = decoded.shape[:2]
    # top padding wanted: 100*0.6=60, more than the 50px of headroom -> clamps
    # to y=0 (uses all of it); bottom padding 100*0.25=25 -> y=175. h=175-0.
    assert h == 175


def test_too_small_box_is_rejected():
    frame = _make_frame_jpeg(200, 200)
    crop = crop_person(frame, (10, 10, 20, 20), padding=0.25, min_size_px=40)
    assert crop is None


def test_badly_clipped_box_is_rejected():
    frame = _make_frame_jpeg(200, 200)
    # Only 1/4 of this 80x40 box actually lies within the 200x200 frame.
    crop = crop_person(frame, (180, 80, 260, 120), padding=0.25, min_visible_ratio=0.85)
    assert crop is None


def test_box_entirely_outside_frame_returns_none_without_crashing():
    frame = _make_frame_jpeg(200, 200)
    crop = crop_person(frame, (250, 250, 300, 300), padding=0.25)
    assert crop is None


def test_zero_area_box_returns_none():
    frame = _make_frame_jpeg(200, 200)
    assert crop_person(frame, (50, 50, 50, 80), padding=0.25) is None
    assert crop_person(frame, (50, 50, 80, 50), padding=0.25) is None


def test_undecodable_frame_returns_none():
    assert crop_person(b"not a jpeg", (0, 0, 10, 10)) is None


def test_save_and_remove_crop_locally():
    frame = _make_frame_jpeg(200, 200)
    crop = crop_person(frame, (50, 50, 150, 150))
    assert crop is not None

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = save_crop_locally(4, crop, tmp_dir)
        assert os.path.exists(path)
        assert os.path.basename(path) == "track_4.jpg"

        remove_crop_locally(4, tmp_dir)
        assert not os.path.exists(path)

        # Removing again (already gone) must not raise.
        remove_crop_locally(4, tmp_dir)


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
