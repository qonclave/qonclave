# SPDX-License-Identifier: MPL-2.0

import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from track_crop import (  # noqa: E402
    FACE_MIN_SIZE_PX, FACE_MIN_VISIBLE_RATIO, POSE_MIN_SIZE_PX,
    POSE_MIN_VISIBLE_RATIO, accepts, crop_person, crop_persons,
    remove_crop_locally, save_crop_locally,
)


def _crop(*args, **kwargs):
    """Just the JPEG bytes.

    crop_person now returns (jpeg, person_box_in_crop) — the rect is what lets
    the hub re-frame for pose. These tests predate that and only care about the
    image, so they unwrap here rather than restating it a dozen times.
    """
    result = crop_person(*args, **kwargs)
    return None if result is None else result[0]


def _make_frame_jpeg(width=200, height=200):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (120, 140, 160)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    return encoded.tobytes()


def test_normal_box_is_cropped_with_padding_and_clamped():
    frame = _make_frame_jpeg(200, 200)
    crop = _crop(frame, (50, 50, 150, 150), padding=0.25, padding_top=0.25, min_size_px=40)
    assert crop is not None
    decoded = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = decoded.shape[:2]
    # box=100x100, padding=0.25 on every side -> +25px each -> 150x150, fully in-frame.
    assert (w, h) == (150, 150)


def test_padding_is_clamped_to_frame_bounds():
    frame = _make_frame_jpeg(200, 200)
    # Box touches the top-left corner; padding would go negative without clamping.
    crop = _crop(frame, (0, 0, 60, 60), padding=0.5, padding_top=0.5, min_size_px=40)
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
    crop = _crop(frame, (80, 80, 120, 120))  # 40x40 box, defaults
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
    crop = _crop(frame, (50, 50, 150, 150), padding=0.25, padding_top=0.6)
    assert crop is not None
    decoded = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, _ = decoded.shape[:2]
    # top padding wanted: 100*0.6=60, more than the 50px of headroom -> clamps
    # to y=0 (uses all of it); bottom padding 100*0.25=25 -> y=175. h=175-0.
    assert h == 175


def test_too_small_box_is_rejected():
    frame = _make_frame_jpeg(200, 200)
    crop = _crop(frame, (10, 10, 20, 20), padding=0.25, min_size_px=40)
    assert crop is None


def test_badly_clipped_box_is_rejected():
    frame = _make_frame_jpeg(200, 200)
    # Only 1/4 of this 80x40 box actually lies within the 200x200 frame.
    crop = _crop(frame, (180, 80, 260, 120), padding=0.25, min_visible_ratio=0.85)
    assert crop is None


def test_box_entirely_outside_frame_returns_none_without_crashing():
    frame = _make_frame_jpeg(200, 200)
    crop = _crop(frame, (250, 250, 300, 300), padding=0.25)
    assert crop is None


def test_zero_area_box_returns_none():
    frame = _make_frame_jpeg(200, 200)
    assert _crop(frame, (50, 50, 50, 80), padding=0.25) is None
    assert _crop(frame, (50, 50, 80, 50), padding=0.25) is None


def test_undecodable_frame_returns_none():
    assert _crop(b"not a jpeg", (0, 0, 10, 10)) is None


def test_save_and_remove_crop_locally():
    frame = _make_frame_jpeg(200, 200)
    crop = _crop(frame, (50, 50, 150, 150))
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


# --- person box inside the crop --------------------------------------------

def test_crop_returns_the_person_rect_inside_it():
    """The rect is what lets the hub re-frame for pose. The crop's padding is
    face-tuned, so without this the person fills about half the frame a
    top-down pose model expects to be full."""
    frame = _make_frame_jpeg(200, 200)
    result = crop_person(frame, (50, 50, 150, 150), padding=0.25, padding_top=0.25)
    assert result is not None
    _jpeg, rect = result
    x1, y1, x2, y2 = rect
    # crop origin is (25, 25); the unpadded box starts at (50, 50).
    assert (x1, y1) == (25, 25)
    assert (x2 - x1, y2 - y1) == (100, 100)


def test_person_rect_is_clamped_into_the_crop():
    """A box running off the frame edge must not produce a negative rect the
    hub would then reject."""
    frame = _make_frame_jpeg(200, 200)
    result = crop_person(frame, (0, 0, 80, 80), padding=0.5, padding_top=0.5,
                         min_visible_ratio=0.5)
    assert result is not None
    _jpeg, (x1, y1, x2, y2) = result
    assert x1 >= 0 and y1 >= 0
    assert x2 > x1 and y2 > y1


def test_face_padding_really_does_shrink_the_person_share():
    """The measurement the pose re-framing exists to correct: with the default
    padding_top=0.8 the person occupies roughly half the crop's height."""
    frame = _make_frame_jpeg(400, 400)
    result = crop_person(frame, (150, 150, 250, 250))
    assert result is not None
    jpeg, (_x1, y1, _x2, y2) = result
    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    share = (y2 - y1) / decoded.shape[0]
    assert 0.4 < share < 0.6, f"person fills {share:.0%} of the crop"


# --- multi-box, one decode --------------------------------------------------

def test_crop_persons_returns_one_entry_per_accepted_box():
    frame = _make_frame_jpeg(400, 400)
    out = crop_persons(frame, {1: (50, 50, 150, 150), 2: (200, 200, 300, 300)})
    assert set(out) == {1, 2}
    for _jpeg, rect in out.values():
        assert len(rect) == 4


def test_crop_persons_omits_rejected_boxes():
    """An empty or partial result is normal, not an error."""
    frame = _make_frame_jpeg(400, 400)
    out = crop_persons(frame, {1: (50, 50, 150, 150), 2: (0, 0, 5, 5)})
    assert set(out) == {1}


def test_crop_persons_on_no_boxes_is_empty():
    assert crop_persons(_make_frame_jpeg(), {}) == {}


def test_crop_persons_on_a_bad_frame_is_empty():
    assert crop_persons(b"not a jpeg", {1: (0, 0, 10, 10)}) == {}


def test_crop_persons_matches_crop_person():
    """The single-box wrapper and the batch path must agree, or a track's crop
    would depend on how many people were in shot."""
    frame = _make_frame_jpeg(400, 400)
    box = (50, 50, 150, 150)
    single = crop_person(frame, box)
    batch = crop_persons(frame, {7: box})
    assert single is not None and 7 in batch
    assert single[1] == batch[7][1]


# --- per-analyzer acceptance ------------------------------------------------

def test_pose_rejects_a_person_too_small_for_limbs():
    """40px upscaled to a 256px input is a 6x stretch: enough to ask 'is there a
    face', useless for limb positions."""
    box = (100, 100, 140, 140)  # 40px tall
    assert accepts(box, 400, 400, FACE_MIN_SIZE_PX, FACE_MIN_VISIBLE_RATIO)
    assert not accepts(box, 400, 400, POSE_MIN_SIZE_PX, POSE_MIN_VISIBLE_RATIO)


def test_pose_accepts_a_person_falling_out_of_frame():
    """The case face thresholds get actively wrong. A person who has fallen near
    the frame edge is exactly the event of interest; 0.85 silently drops them."""
    box = (-60, 100, 140, 300)  # 200px tall, ~70% visible
    assert not accepts(box, 400, 400, FACE_MIN_SIZE_PX, FACE_MIN_VISIBLE_RATIO)
    assert accepts(box, 400, 400, POSE_MIN_SIZE_PX, POSE_MIN_VISIBLE_RATIO)


def test_both_reject_a_degenerate_box():
    for box in ((10, 10, 10, 50), (10, 10, 50, 10), (500, 500, 600, 600)):
        assert not accepts(box, 400, 400, FACE_MIN_SIZE_PX, FACE_MIN_VISIBLE_RATIO)
        assert not accepts(box, 400, 400, POSE_MIN_SIZE_PX, POSE_MIN_VISIBLE_RATIO)
