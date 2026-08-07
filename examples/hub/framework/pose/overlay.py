"""
overlay.py — draw a COCO-17 skeleton over a person crop.

Used by the hub's /track/analyze pipeline to write the per-track annotated
frames (hub/track_frames/track_<id>.jpg) that GET /user/tracks/<id>.jpg
serves. Colour convention from the prototype: right side warm (orange), left
side cool (blue), joints green.
"""

from __future__ import annotations

import logging

log = logging.getLogger("qonclave.pose")

# COCO-17 skeleton edge list (indices into pose_pipeline.KEYPOINT_NAMES).
SKELETON = [(15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12),
            (5, 6), (5, 7), (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2),
            (1, 3), (2, 4), (3, 5), (4, 6)]
# limb colors (BGR): right side warm, left side cool
_RIGHT = (2, 4, 6, 8, 10, 12, 14, 16)
COLORS = [(0, 170, 255) if a in _RIGHT or b in _RIGHT else (255, 170, 0)
          for a, b in SKELETON]

KP_THRESHOLD = 0.12  # min keypoint score to draw


def draw_pose_overlay(crop_jpeg: bytes, keypoints, label: str = "",
                      kp_thresh: float = KP_THRESHOLD) -> "bytes | None":
    """Draw the skeleton (and an optional label) onto a JPEG crop.

    keypoints: 17 x [x, y, score] in the crop's pixel space, as returned by
    PoseBackend.estimate(). Returns re-encoded JPEG bytes, or None if the
    input can't be decoded/encoded — never raises.
    """
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(crop_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return None

        kps = np.asarray(keypoints, dtype=np.float32)
        if kps.ndim != 2 or kps.shape[1] != 3:
            return None

        for (a, b), col in zip(SKELETON, COLORS):
            if a < len(kps) and b < len(kps) \
                    and kps[a, 2] >= kp_thresh and kps[b, 2] >= kp_thresh:
                cv2.line(frame, (int(kps[a, 0]), int(kps[a, 1])),
                         (int(kps[b, 0]), int(kps[b, 1])), col, 2, cv2.LINE_AA)
        for j in range(len(kps)):
            if kps[j, 2] >= kp_thresh:
                cv2.circle(frame, (int(kps[j, 0]), int(kps[j, 1])), 3,
                           (60, 255, 60), -1, cv2.LINE_AA)

        if label:
            cv2.putText(frame, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (255, 255, 255), 1, cv2.LINE_AA)

        ok, encoded = cv2.imencode(".jpg", frame)
        return encoded.tobytes() if ok else None
    except Exception:
        log.exception("draw_pose_overlay failed")
        return None
