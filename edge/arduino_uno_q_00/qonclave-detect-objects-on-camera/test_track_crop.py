# SPDX-License-Identifier: MPL-2.0

import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from track_crop import crop_person, crop_persons, remove_crop_locally, save_crop_locally  # noqa: E402


def _make_frame_jpeg(width=200, height=200):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (120, 140, 160)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    return encoded.tobytes()


def _decode(crop_jpeg):
    return cv2.imdecode(np.frombuffer(crop_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)


def test_normal_box_is_cropped_with_padding_and_clamped():
    frame = _make_frame_jpeg(200, 200)
    result = crop_person(frame, (50, 50, 150, 150), padding=0.25, padding_top=0.25, min_size_px=40)
    assert result is not None
    crop, _person_box = result
    h, w = _decode(crop).shape[:2]
    # box=100x100, padding=0.25 on every side -> +25px each -> 150x150, fully in-frame.
    assert (w, h) == (150, 150)


def test_padding_is_clamped_to_frame_bounds():
    frame = _make_frame_jpeg(200, 200)
    # Box touches the top-left corner; padding would go negative without clamping.
    result = crop_person(frame, (0, 0, 60, 60), padding=0.5, padding_top=0.5, min_size_px=40)
    assert result is not None
    h, w = _decode(result[0]).shape[:2]
    assert w <= 200 and h <= 200
    assert w >= 40 and h >= 40


def test_top_gets_more_padding_than_bottom_by_default():
    frame = _make_frame_jpeg(200, 200)
    # Box centered with room to spare on every side: padding_top (default 0.8)
    # should push the top edge up further than padding (default 0.25) pushes
    # the bottom edge down.
    result = crop_person(frame, (80, 80, 120, 120))  # 40x40 box, defaults
    assert result is not None
    h, w = _decode(result[0]).shape[:2]
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
    result = crop_person(frame, (50, 50, 150, 150), padding=0.25, padding_top=0.6)
    assert result is not None
    h, _ = _decode(result[0]).shape[:2]
    # top padding wanted: 100*0.6=60, more than the 50px of headroom -> clamps
    # to y=0 (uses all of it); bottom padding 100*0.25=25 -> y=175. h=175-0.
    assert h == 175


def test_person_box_is_reported_relative_to_the_crop():
    frame = _make_frame_jpeg(200, 200)
    # Box (80,80)-(120,120), defaults: crop spans x=[70,130], y=[48,130] (see
    # the padding test above). The unpadded box inside that crop must land at
    # (80-70, 80-48, 120-70, 120-48).
    result = crop_person(frame, (80, 80, 120, 120))
    assert result is not None
    _crop, person_box = result
    assert person_box == (10, 32, 50, 72)


def test_person_box_is_clamped_when_padding_hits_the_frame_edge():
    frame = _make_frame_jpeg(200, 200)
    # Box touches the top-left corner: the crop starts at (0, 0), so the
    # person box in crop coordinates is the box itself, unshifted.
    result = crop_person(frame, (0, 0, 60, 60), padding=0.5, padding_top=0.5)
    assert result is not None
    _crop, person_box = result
    assert person_box == (0, 0, 60, 60)


def test_too_small_box_is_rejected():
    frame = _make_frame_jpeg(200, 200)
    assert crop_person(frame, (10, 10, 20, 20), padding=0.25, min_size_px=40) is None


def test_badly_clipped_box_is_rejected():
    frame = _make_frame_jpeg(200, 200)
    # Only 1/4 of this 80x40 box actually lies within the 200x200 frame.
    assert crop_person(frame, (180, 80, 260, 120), padding=0.25, min_visible_ratio=0.85) is None


def test_box_entirely_outside_frame_returns_none_without_crashing():
    frame = _make_frame_jpeg(200, 200)
    assert crop_person(frame, (250, 250, 300, 300), padding=0.25) is None


def test_zero_area_box_returns_none():
    frame = _make_frame_jpeg(200, 200)
    assert crop_person(frame, (50, 50, 50, 80), padding=0.25) is None
    assert crop_person(frame, (50, 50, 80, 50), padding=0.25) is None


def test_undecodable_frame_returns_none():
    assert crop_person(b"not a jpeg", (0, 0, 10, 10)) is None


def test_save_and_remove_crop_locally():
    frame = _make_frame_jpeg(200, 200)
    result = crop_person(frame, (50, 50, 150, 150))
    assert result is not None

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = save_crop_locally(4, result[0], tmp_dir)
        assert os.path.exists(path)
        assert os.path.basename(path) == "track_4.jpg"

        remove_crop_locally(4, tmp_dir)
        assert not os.path.exists(path)

        # Removing again (already gone) must not raise.
        remove_crop_locally(4, tmp_dir)


# --- crop_persons: decode-once multi-box + per-analyzer gates ----------------

def test_crop_persons_crops_every_accepted_box():
    frame = _make_frame_jpeg(400, 400)
    out = crop_persons(frame, {1: (50, 50, 160, 250), 2: (200, 100, 320, 300)})
    assert set(out.keys()) == {1, 2}
    for entry in out.values():
        assert _decode(entry["jpeg"]) is not None
        x1, y1, x2, y2 = entry["person_box"]
        assert x2 > x1 and y2 > y1
        assert entry["analyzers_ok"] == {"face", "pose"}


def test_crop_persons_matches_crop_person_geometry():
    frame = _make_frame_jpeg(200, 200)
    single = crop_person(frame, (80, 80, 120, 180))
    multi = crop_persons(frame, {7: (80, 80, 120, 180)})
    assert single is not None and 7 in multi
    assert _decode(single[0]).shape == _decode(multi[7]["jpeg"]).shape
    assert single[1] == multi[7]["person_box"]


def test_small_person_samples_face_but_not_pose():
    frame = _make_frame_jpeg(200, 200)
    # 40x60 box: fine for "is there a face" (min 40px crop), far below the
    # 100px of body height pose needs for usable limb pixels.
    out = crop_persons(frame, {1: (80, 80, 120, 140)})
    assert out[1]["analyzers_ok"] == {"face"}


def test_edge_clipped_person_samples_pose_but_not_face():
    frame = _make_frame_jpeg(200, 200)
    # Half of this box is outside the frame: rejected by face's 0.85
    # visibility gate, but a person half out of frame is exactly the
    # fall-detection case pose must not drop (0.5 gate, box height 160).
    out = crop_persons(frame, {1: (120, 20, 280, 180)})
    assert out[1]["analyzers_ok"] == {"pose"}


def test_crop_persons_drops_box_no_analyzer_accepts():
    frame = _make_frame_jpeg(200, 200)
    out = crop_persons(frame, {1: (10, 10, 20, 20),      # too small for both
                               2: (250, 250, 300, 300),  # fully offscreen
                               3: (50, 50, 160, 190)})   # fine
    assert set(out.keys()) == {3}


def test_crop_persons_undecodable_frame_returns_empty():
    assert crop_persons(b"not a jpeg", {1: (0, 0, 100, 150)}) == {}


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
