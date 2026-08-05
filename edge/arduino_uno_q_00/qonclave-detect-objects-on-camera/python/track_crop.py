# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
track_crop.py -- crop tracked people out of the full camera frame, for the
hub's per-track analysis endpoint (POST /track/analyze).

Reuses cv2 (already an implicit dependency of this app via file_camera.py and
the video_objectdetection brick, so no new package is added) to decode the JPEG
frame main.py already has on hand, crop with padding, and re-encode.

Three things here exist because ONE crop now serves TWO analyzers with different
needs:

* **The crop returns the person's rect inside it.** The padding is face-tuned
  (`padding_top=0.8`, so a face at the top of the box is not clipped), which
  makes the person fill only ~49% of the crop, offset into its lower half. Face
  detection wants that; a top-down pose model wants a tight box with the subject
  filling the frame. Rather than send a second crop, the unpadded rect travels
  with the first one and the hub re-frames from it.

* **Rejection thresholds are per analyzer.** `min_size_px=40` and
  `min_visible_ratio=0.85` are face-tuned too. A 40 px person stretched to a
  256 px pose input is useless for limb positions, and 0.85 is actively harmful
  for fall detection — a person who has fallen near the frame edge is exactly
  the event of interest, and that threshold silently drops them.

* **`crop_persons` decodes the frame once.** At 4 Hz across N tracks, one
  `cv2.imdecode` per track per sample is real CPU on the UNO Q.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

# Per-analyzer crop acceptance. A crop is worth sending if EITHER analyzer would
# take it; the hub then skips whichever one the crop is too poor for.
FACE_MIN_SIZE_PX = 40
FACE_MIN_VISIBLE_RATIO = 0.85
POSE_MIN_SIZE_PX = 100          # box height; below this, limb positions are noise
POSE_MIN_VISIBLE_RATIO = 0.5    # a person falling out of frame is the event, not an error


def crop_person(
    frame_jpeg: bytes,
    bounding_box_xyxy: "tuple[float, float, float, float]",
    padding: float = 0.25,
    padding_top: float = 0.8,
    min_size_px: int = FACE_MIN_SIZE_PX,
    min_visible_ratio: float = FACE_MIN_VISIBLE_RATIO,
) -> "tuple[bytes, tuple[int, int, int, int]] | None":
    """Crop one detected person out of a full frame.

    Single-box wrapper over `crop_persons`, kept so existing callers and tests
    read the same. Returns `(jpeg_bytes, person_box_in_crop)` or None if the
    crop should be rejected.

    `person_box_in_crop` is the UNPADDED box expressed relative to the crop's
    own origin — what the hub needs to re-frame for pose.
    """
    frame = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    return crop_from_frame(frame, bounding_box_xyxy, padding, padding_top,
                           min_size_px, min_visible_ratio)


def crop_persons(
    frame_jpeg: bytes,
    boxes: "dict[int, tuple[float, float, float, float]]",
    padding: float = 0.25,
    padding_top: float = 0.8,
    min_size_px: int = FACE_MIN_SIZE_PX,
    min_visible_ratio: float = FACE_MIN_VISIBLE_RATIO,
) -> "dict[int, tuple[bytes, tuple[int, int, int, int]]]":
    """Crop several tracks out of ONE decode.

    `boxes` maps track_id -> bounding box. Returns track_id -> (jpeg, rect),
    omitting any track whose crop was rejected — so an empty result is normal,
    not an error.
    """
    if not boxes:
        return {}
    frame = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return {}

    out = {}
    for track_id, box in boxes.items():
        cropped = crop_from_frame(frame, box, padding, padding_top,
                                  min_size_px, min_visible_ratio)
        if cropped is not None:
            out[track_id] = cropped
    return out


def crop_from_frame(
    frame,
    bounding_box_xyxy,
    padding: float = 0.25,
    padding_top: float = 0.8,
    min_size_px: int = FACE_MIN_SIZE_PX,
    min_visible_ratio: float = FACE_MIN_VISIBLE_RATIO,
) -> "tuple[bytes, tuple[int, int, int, int]] | None":
    """The crop itself, on an already-decoded frame."""
    h, w = frame.shape[:2]

    x1, y1, x2, y2 = bounding_box_xyxy
    box_w, box_h = x2 - x1, y2 - y1
    if box_w <= 0 or box_h <= 0:
        return None

    visible_w = min(x2, w) - max(x1, 0)
    visible_h = min(y2, h) - max(y1, 0)
    if visible_w <= 0 or visible_h <= 0:
        return None
    if (visible_w * visible_h) / (box_w * box_h) < min_visible_ratio:
        return None

    pad_x = box_w * padding
    ix1 = max(0, int(x1 - pad_x))
    iy1 = max(0, int(y1 - box_h * padding_top))
    ix2 = min(w, int(x2 + pad_x))
    iy2 = min(h, int(y2 + box_h * padding))
    if ix2 - ix1 < min_size_px or iy2 - iy1 < min_size_px:
        return None

    crop = frame[iy1:iy2, ix1:ix2]
    ok, encoded = cv2.imencode(".jpg", crop)
    if not ok:
        return None

    # The unpadded box, relative to the crop's origin. Clamped to the crop so a
    # box that ran off the frame edge cannot produce a negative rect the hub
    # would then reject.
    crop_h, crop_w = crop.shape[:2]
    person_box_in_crop = (
        max(0, int(x1) - ix1), max(0, int(y1) - iy1),
        min(crop_w, int(x2) - ix1), min(crop_h, int(y2) - iy1),
    )
    return encoded.tobytes(), person_box_in_crop


def accepts(bounding_box_xyxy, frame_w: int, frame_h: int,
            min_size_px: int, min_visible_ratio: float) -> bool:
    """Whether one analyzer's thresholds would accept this box.

    Used to decide whether a crop is worth sending at all: if either analyzer
    accepts, the crop goes, and the hub skips whichever analyzer it is too poor
    for. Deciding per analyzer on the edge and then sending one crop is what
    keeps this to a single request.
    """
    x1, y1, x2, y2 = bounding_box_xyxy
    box_w, box_h = x2 - x1, y2 - y1
    if box_w <= 0 or box_h <= 0:
        return False
    visible_w = min(x2, frame_w) - max(x1, 0)
    visible_h = min(y2, frame_h) - max(y1, 0)
    if visible_w <= 0 or visible_h <= 0:
        return False
    if (visible_w * visible_h) / (box_w * box_h) < min_visible_ratio:
        return False
    return box_h >= min_size_px


def save_crop_locally(track_id: int, crop_jpeg: bytes, crops_dir: str) -> str:
    """Write/overwrite `track_<id>.jpg` in crops_dir, so a human can visually
    confirm each track's crop shows the right person. One file per track (not
    timestamped) -- it always reflects that track's latest sample."""
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
