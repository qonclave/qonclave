# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
track_crop.py -- crop tracked people out of the full camera frame, for the
hub's per-track-id analysis endpoint (POST /track/analyze).

Reuses cv2 (already an implicit dependency of this app via file_camera.py /
the video_objectdetection brick, so no new package is added) to decode the
JPEG frame bytes main.py already has on hand, crop with padding, and
re-encode.

The crop framing is face-tuned on purpose (padding_top=0.8 so a face at the
box's top edge isn't clipped); each crop is therefore returned WITH the
unpadded person box's position inside it, so the hub's pose analyzer can
re-frame tightly around the person while face ID uses the full crop.

Rejection is per-analyzer: the face and pose profiles need different minimum
sizes (a 40px person upscaled 6x still answers "is there a face" but is
useless for limb positions) and different visibility (a person half out of
frame is a bad face sample but exactly the fall-detection case that matters).
A crop is produced if EITHER analyzer would accept it; which ones did is
reported so the caller can request only those.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

# Face profile (unchanged from the original face-only pipeline).
FACE_MIN_SIZE_PX = 40
FACE_MIN_VISIBLE_RATIO = 0.85
# Pose profile: HRNet needs real limb pixels (box height, not padded-crop
# size), and tolerates heavy truncation -- a fallen person near the frame
# edge is the event of interest.
POSE_MIN_BOX_HEIGHT_PX = 100
POSE_MIN_VISIBLE_RATIO = 0.5


def _crop_geometry(w: int, h: int, bounding_box_xyxy, padding: float,
                   padding_top: float):
    """Padded-crop geometry for one box in a w x h frame.

    Returns (ix1, iy1, ix2, iy2, visible_ratio, person_box_in_crop) or None
    for a degenerate/fully-offscreen box. person_box_in_crop is the unpadded
    box clamped into the crop's own pixel space.
    """
    x1, y1, x2, y2 = bounding_box_xyxy
    box_w, box_h = x2 - x1, y2 - y1
    if box_w <= 0 or box_h <= 0:
        return None

    visible_w = min(x2, w) - max(x1, 0)
    visible_h = min(y2, h) - max(y1, 0)
    if visible_w <= 0 or visible_h <= 0:
        return None
    visible_ratio = (visible_w * visible_h) / (box_w * box_h)

    pad_x = box_w * padding
    ix1 = max(0, int(x1 - pad_x))
    iy1 = max(0, int(y1 - box_h * padding_top))
    ix2 = min(w, int(x2 + pad_x))
    iy2 = min(h, int(y2 + box_h * padding))
    if ix2 - ix1 <= 0 or iy2 - iy1 <= 0:
        return None

    person_box_in_crop = (
        max(0, int(x1) - ix1),
        max(0, int(y1) - iy1),
        min(ix2 - ix1, int(x2) - ix1),
        min(iy2 - iy1, int(y2) - iy1),
    )
    return ix1, iy1, ix2, iy2, visible_ratio, person_box_in_crop


def _analyzers_ok(crop_w: int, crop_h: int, box_h: float, visible_ratio: float,
                  min_size_px: int, min_visible_ratio: float,
                  pose_min_box_height_px: int, pose_min_visible_ratio: float) -> set:
    """Which analyzers accept a crop with this geometry."""
    ok = set()
    if (visible_ratio >= min_visible_ratio
            and crop_w >= min_size_px and crop_h >= min_size_px):
        ok.add("face")
    if box_h >= pose_min_box_height_px and visible_ratio >= pose_min_visible_ratio:
        ok.add("pose")
    return ok


def crop_person(
    frame_jpeg: bytes,
    bounding_box_xyxy: tuple[float, float, float, float],
    padding: float = 0.25,
    padding_top: float = 0.8,
    min_size_px: int = FACE_MIN_SIZE_PX,
    min_visible_ratio: float = FACE_MIN_VISIBLE_RATIO,
) -> "tuple[bytes, tuple[int, int, int, int]] | None":
    """Crop a detected person's bounding box out of a full frame.

    frame_jpeg: the full camera frame as already-encoded JPEG bytes (the same
        bytes main.py's detection callback forwards to the hub for
        /edge/event).
    bounding_box_xyxy: (x1, y1, x2, y2) in the same pixel space as
        `camera.resolution`, exactly as PersonTracker reports it.
    padding: fraction of the box's width/height added on the left, right,
        and bottom edges.
    padding_top: fraction of the box's height added above it -- larger than
        `padding` by default, because a person's face sits right at the top
        of their box: the person-detector's box is sometimes tight there, and
        a face right at the crop's edge is exactly what the hub's face
        detector struggles with. Send the whole padded person box and let the
        hub find + crop the actual face from it, rather than risk clipping
        it here.
    min_size_px: reject the crop if either dimension (after padding and
        clamping to frame bounds) is smaller than this.
    min_visible_ratio: reject the crop if less than this fraction of the
        *unpadded* box actually lies within the frame -- i.e. the person is
        mostly cut off by the frame edge, not just tightly boxed.

    Returns (jpeg_bytes, person_box_in_crop) -- the unpadded box's rect in
    the crop's own pixels, for the hub's pose re-framing -- or None if the
    crop should be rejected. This single-box wrapper keeps the face-tuned
    gate; multi-analyzer gating lives in crop_persons().
    """
    frame = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    h, w = frame.shape[:2]

    geo = _crop_geometry(w, h, bounding_box_xyxy, padding, padding_top)
    if geo is None:
        return None
    ix1, iy1, ix2, iy2, visible_ratio, person_box_in_crop = geo

    if visible_ratio < min_visible_ratio:
        return None
    if ix2 - ix1 < min_size_px or iy2 - iy1 < min_size_px:
        return None

    crop = frame[iy1:iy2, ix1:ix2]
    ok, encoded = cv2.imencode(".jpg", crop)
    if not ok:
        return None
    return encoded.tobytes(), person_box_in_crop


def crop_persons(
    frame_jpeg: bytes,
    boxes_by_track: "dict[int, tuple[float, float, float, float]]",
    padding: float = 0.25,
    padding_top: float = 0.8,
    face_min_size_px: int = FACE_MIN_SIZE_PX,
    face_min_visible_ratio: float = FACE_MIN_VISIBLE_RATIO,
    pose_min_box_height_px: int = POSE_MIN_BOX_HEIGHT_PX,
    pose_min_visible_ratio: float = POSE_MIN_VISIBLE_RATIO,
) -> dict:
    """Crop every tracked person out of one frame, decoding the JPEG ONCE.

    At ~4 Hz across N tracks, one cv2.imdecode per track per sample is real
    CPU cost on the UNO Q -- main.py calls this once per detection callback
    with every due track's box.

    Returns {track_id: {"jpeg": bytes, "person_box": (x1, y1, x2, y2),
                        "analyzers_ok": set[str]}}, with entries only for
    crops at least one analyzer accepts. person_box is the unpadded box in
    the crop's own pixels.
    """
    out: dict = {}
    frame = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return out
    h, w = frame.shape[:2]

    for track_id, box in boxes_by_track.items():
        geo = _crop_geometry(w, h, box, padding, padding_top)
        if geo is None:
            continue
        ix1, iy1, ix2, iy2, visible_ratio, person_box_in_crop = geo

        analyzers_ok = _analyzers_ok(
            ix2 - ix1, iy2 - iy1, box[3] - box[1], visible_ratio,
            face_min_size_px, face_min_visible_ratio,
            pose_min_box_height_px, pose_min_visible_ratio,
        )
        if not analyzers_ok:
            continue

        ok, encoded = cv2.imencode(".jpg", frame[iy1:iy2, ix1:ix2])
        if not ok:
            continue
        out[track_id] = {
            "jpeg": encoded.tobytes(),
            "person_box": person_box_in_crop,
            "analyzers_ok": analyzers_ok,
        }
    return out


def save_crop_locally(track_id: int, crop_jpeg: bytes, crops_dir: str) -> str:
    """Write/overwrite `track_<id>.jpg` in crops_dir, so a human can visually
    confirm each track's crop shows the right person. One file per track
    (not timestamped) -- it always reflects that track's latest sample."""
    os.makedirs(crops_dir, exist_ok=True)
    path = os.path.join(crops_dir, f"track_{track_id}.jpg")
    with open(path, "wb") as f:
        f.write(crop_jpeg)
    return path


def remove_crop_locally(track_id: int, crops_dir: str) -> None:
    """Best-effort cleanup once a track is no longer active."""
    try:
        os.remove(os.path.join(crops_dir, f"track_{track_id}.jpg"))
    except OSError:
        pass
