# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
track_overlay.py -- draws each tracked person's box + label on the camera
frame, for the live preview feed (main.py's /track-preview route).

Pure function, no state: main.py owns the "latest annotated frame" cache and
the MJPEG streaming loop; this module only turns one frame + this frame's
person tracks into one annotated JPEG.
"""

from __future__ import annotations

import cv2
import numpy as np

_BOX_COLOR = (0, 200, 255)  # BGR: amber, matches this app's AI-badge color family
_LABEL_TEXT_COLOR = (20, 20, 20)
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FONT_SCALE = 0.6
_FONT_THICKNESS = 2


def _draw_tracks(frame: np.ndarray, tracks: list[dict], labels: dict) -> None:
    """Draw each track's box + label onto frame, in place."""
    for track in tracks:
        track_id = track["track_id"]
        x1, y1, x2, y2 = (int(v) for v in track["bounding_box_xyxy"])
        label = labels.get(track_id, f"Track {track_id}")

        cv2.rectangle(frame, (x1, y1), (x2, y2), _BOX_COLOR, 2)

        (text_w, text_h), _ = cv2.getTextSize(label, _FONT, _FONT_SCALE, _FONT_THICKNESS)
        label_bottom = max(text_h + 6, y1)
        cv2.rectangle(
            frame,
            (x1, label_bottom - text_h - 8),
            (x1 + text_w + 8, label_bottom),
            _BOX_COLOR,
            -1,
        )
        cv2.putText(
            frame, label, (x1 + 4, label_bottom - 4),
            _FONT, _FONT_SCALE, _LABEL_TEXT_COLOR, _FONT_THICKNESS,
        )


def draw_track_overlay(frame_jpeg: bytes, tracks: list[dict], labels: dict) -> bytes:
    """Return frame_jpeg re-encoded with each track's box + label drawn.

    tracks: this frame's person_tracks (each needs at least "track_id" and
        "bounding_box_xyxy").
    labels: track_id -> display text (e.g. "Track 4: Jogendra"). A track
        missing from labels still gets a box, labeled "Track <id>".

    Returns the original bytes unchanged if the frame can't be decoded, so a
    corrupt/unexpected frame never breaks the preview stream.
    """
    frame = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return frame_jpeg
    _draw_tracks(frame, tracks, labels)
    ok, encoded = cv2.imencode(".jpg", frame)
    return encoded.tobytes() if ok else frame_jpeg


def draw_track_overlay_bgr(frame_bgr: np.ndarray, tracks: list[dict], labels: dict) -> bytes | None:
    """Like draw_track_overlay, but from a decoded BGR frame: skips the JPEG
    decode, which matters on the camera-rate preview path.

    Draws onto frame_bgr in place -- pass a copy if the caller (or another
    thread) still needs the original pixels. Returns None if encoding fails.
    """
    _draw_tracks(frame_bgr, tracks, labels)
    ok, encoded = cv2.imencode(".jpg", frame_bgr)
    return encoded.tobytes() if ok else None


def encode_jpeg(frame_bgr: np.ndarray) -> bytes | None:
    """JPEG-encode a BGR frame; None if encoding fails."""
    ok, encoded = cv2.imencode(".jpg", frame_bgr)
    return encoded.tobytes() if ok else None
