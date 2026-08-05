"""
overlay.py — draw a COCO-17 skeleton onto a crop.

Kept separate from `pose.py` so inference never depends on a drawing library,
and so a failed overlay can never fail an estimate. The colour scheme is
left-cool / right-warm, which is the fastest way to see a left/right swap — the
most common way a top-down pose model goes wrong on a person facing away.
"""

from __future__ import annotations

import logging

log = logging.getLogger("qonclave.pose")

# COCO-17 skeleton, as (from, to) keypoint indices.
SKELETON = (
    (15, 13), (13, 11), (16, 14), (14, 12), (11, 12),   # legs + hips
    (5, 11), (6, 12), (5, 6),                            # torso
    (5, 7), (7, 9), (6, 8), (8, 10),                     # arms
    (1, 2), (0, 1), (0, 2), (1, 3), (2, 4),              # head
)

# BGR, because OpenCV. Cool = left, warm = right.
LEFT_COLOR = (255, 176, 0)
RIGHT_COLOR = (0, 140, 255)
CENTER_COLOR = (200, 200, 200)

LEFT_KEYPOINTS = frozenset({1, 3, 5, 7, 9, 11, 13, 15})
RIGHT_KEYPOINTS = frozenset({2, 4, 6, 8, 10, 12, 14, 16})

MIN_DRAW_SCORE = 0.1


def _color_for(index: int):
    if index in LEFT_KEYPOINTS:
        return LEFT_COLOR
    if index in RIGHT_KEYPOINTS:
        return RIGHT_COLOR
    return CENTER_COLOR


def draw_pose_overlay(crop_jpeg: bytes, keypoints, label: str | None = None) -> bytes:
    """Return `crop_jpeg` with a skeleton drawn on it.

    Best-effort: on any failure the ORIGINAL bytes come back rather than an
    exception or a blank image. This is decoration on a retention artifact, and
    losing the decoration is always better than losing the frame.
    """
    if not crop_jpeg or not keypoints:
        return crop_jpeg

    try:
        import cv2
        import numpy as np

        buf = np.frombuffer(crop_jpeg, dtype=np.uint8)
        image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if image is None:
            return crop_jpeg

        h, w = image.shape[:2]

        def visible(kp):
            x, y, score = kp
            return score >= MIN_DRAW_SCORE and 0 <= x < w and 0 <= y < h

        for a, b in SKELETON:
            if a >= len(keypoints) or b >= len(keypoints):
                continue
            ka, kb = keypoints[a], keypoints[b]
            if not (visible(ka) and visible(kb)):
                continue
            cv2.line(image, (int(ka[0]), int(ka[1])), (int(kb[0]), int(kb[1])),
                     _color_for(a), 2, cv2.LINE_AA)

        for i, kp in enumerate(keypoints):
            if not visible(kp):
                continue
            cv2.circle(image, (int(kp[0]), int(kp[1])), 3, _color_for(i), -1, cv2.LINE_AA)

        if label:
            cv2.putText(image, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(image, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 255), 1, cv2.LINE_AA)

        ok, encoded = cv2.imencode(".jpg", image)
        return encoded.tobytes() if ok else crop_jpeg
    except Exception as e:
        log.debug("pose overlay failed, returning the original crop: %s", e)
        return crop_jpeg
