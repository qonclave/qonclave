# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
track_crop.py -- crop one tracked person out of the full camera frame, for
the hub's per-track-id face recognition endpoint (POST /recognize).

Reuses cv2 (already an implicit dependency of this app via file_camera.py /
the video_objectdetection brick, so no new package is added) to decode the
JPEG frame bytes main.py already has on hand, crop with padding, and
re-encode. Rejects crops that are too small or mostly clipped off-frame --
those make useless face-recognition samples and aren't worth a hub round trip.
"""

from __future__ import annotations

import os

import cv2
import numpy as np


def crop_person(
    frame_jpeg: bytes,
    bounding_box_xyxy: tuple[float, float, float, float],
    padding: float = 0.25,
    padding_top: float = 0.8,
    min_size_px: int = 40,
    min_visible_ratio: float = 0.85,
) -> "bytes | None":
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

    Returns re-encoded JPEG bytes, or None if the crop should be rejected.
    """
    frame = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    h, w = frame.shape[:2]

    x1, y1, x2, y2 = bounding_box_xyxy
    box_w, box_h = x2 - x1, y2 - y1
    if box_w <= 0 or box_h <= 0:
        return None

    visible_w = min(x2, w) - max(x1, 0)
    visible_h = min(y2, h) - max(y1, 0)
    if visible_w <= 0 or visible_h <= 0:
        return None
    visible_ratio = (visible_w * visible_h) / (box_w * box_h)
    if visible_ratio < min_visible_ratio:
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
    return encoded.tobytes()


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
