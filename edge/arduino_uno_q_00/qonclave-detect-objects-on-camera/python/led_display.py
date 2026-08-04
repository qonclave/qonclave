# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
led_display.py -- maps a tracked person's centroid (in camera frame pixel
space, e.g. from person_tracker.py) onto the UNO Q's 12x8 onboard LED
matrix.

The position indicator is constrained to the matrix's outer ring (row 0,
row 7, and columns 0/11 of the rows in between) via a ray-cast from the
frame's center through the centroid, projected outward onto the ring --
this keeps the dot moving smoothly all the way around the border as the
person moves anywhere in frame, and never encroaches on the interior. The
freed-up interior (6 rows x 10 cols) is reserved for a person "emotion"
indicator -- eventually synthesized by an LLM (mirroring the existing
per-object icon pipeline), a hardcoded smiley for now.

Dependency-free, matching this app's existing minimal-dependency footprint.
"""

from __future__ import annotations

GRID_COLS = 12
GRID_ROWS = 8

# 6 rows x 10 cols, placed at offset (1, 1) inside the 8x12 grid so it never
# touches the outer ring reserved for the position indicator.
SMILEY_BITMAP = [
    [0, 0, 1, 1, 0, 0, 1, 1, 0, 0],
    [0, 0, 1, 1, 0, 0, 1, 1, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 1, 0, 0, 0, 0, 0, 0, 1, 0],
    [0, 0, 1, 1, 1, 1, 1, 1, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def person_position_bitmap(centroid: tuple[float, float], frame_width: int, frame_height: int) -> list[list[int]]:
    """Project a centroid onto the 12x8 grid's outer ring.

    The centroid's position relative to the frame center is ray-cast
    outward onto the border of the grid, so every centroid -- however far
    off-center -- lands exactly on the ring (never in the interior).

    Args:
        centroid: (x, y) in the camera frame's pixel coordinate space.
        frame_width: Camera frame width in pixels (e.g. camera.resolution[0]).
        frame_height: Camera frame height in pixels (e.g. camera.resolution[1]).

    Returns:
        8 rows x 12 columns of 0/1 ints; only ring cells may be lit.
    """
    cx, cy = centroid
    frame_width = frame_width or 1
    frame_height = frame_height or 1

    nx = _clamp((cx - frame_width / 2) / (frame_width / 2), -1.0, 1.0)
    ny = _clamp((cy - frame_height / 2) / (frame_height / 2), -1.0, 1.0)

    if nx == 0 and ny == 0:
        ny = -1.0  # dead-center: default to straight up rather than divide by zero

    scale = 1.0 / max(abs(nx), abs(ny))
    nx *= scale
    ny *= scale

    col = int(_clamp(round((nx + 1) / 2 * (GRID_COLS - 1)), 0, GRID_COLS - 1))
    row = int(_clamp(round((ny + 1) / 2 * (GRID_ROWS - 1)), 0, GRID_ROWS - 1))

    bitmap = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
    bitmap[row][col] = 1
    # Light a second cell along the same ring edge for visibility -- a
    # single LED is easy to miss -- without spilling into the interior.
    if row in (0, GRID_ROWS - 1):
        bitmap[row][int(_clamp(col + 1, 0, GRID_COLS - 1))] = 1
    else:
        bitmap[int(_clamp(row + 1, 0, GRID_ROWS - 1))][col] = 1

    return bitmap


def emotion_bitmap(name: str = "smiley") -> list[list[int]]:
    """Return a full 8x12 bitmap with an emotion icon in the interior.

    Only "smiley" exists today (hardcoded placeholder); this is where an
    LLM-generated emotion bitmap (analogous to the per-object icon pipeline
    in main.py) will plug in later without changing the call site.
    """
    bitmap = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
    for r, row in enumerate(SMILEY_BITMAP):
        for c, val in enumerate(row):
            bitmap[r + 1][c + 1] = val
    return bitmap


def person_display_bitmap(
    centroid: tuple[float, float],
    frame_width: int,
    frame_height: int,
    emotion: str = "smiley",
) -> list[list[int]]:
    """Compose the ring position indicator with the center emotion icon.

    Safe to OR unconditionally: the position indicator never lights an
    interior cell, and the emotion icon never lights a ring cell.
    """
    position = person_position_bitmap(centroid, frame_width, frame_height)
    emotion_bmp = emotion_bitmap(emotion)
    return [
        [1 if (p or e) else 0 for p, e in zip(prow, erow)]
        for prow, erow in zip(position, emotion_bmp)
    ]
