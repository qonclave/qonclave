# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
led_display.py -- maps a tracked person's centroid (in camera frame pixel
space, e.g. from person_tracker.py) onto the UNO Q's 12x8 onboard LED
matrix, so the physical display can show roughly where the person is in
frame. A single lit LED is easy to miss, so the mapped position is rendered
as a small 2x2 block instead of one pixel; there is no separate direction
glyph -- the dot's position moving across the grid over successive frames is
the direction signal.

Dependency-free, matching this app's existing minimal-dependency footprint.
"""

from __future__ import annotations

GRID_COLS = 12
GRID_ROWS = 8


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def person_position_bitmap(centroid: tuple[float, float], frame_width: int, frame_height: int) -> list[list[int]]:
    """Map a centroid in frame pixel-space to a lit 2x2 block on a 12x8 grid.

    Args:
        centroid: (x, y) in the camera frame's pixel coordinate space.
        frame_width: Camera frame width in pixels (e.g. camera.resolution[0]).
        frame_height: Camera frame height in pixels (e.g. camera.resolution[1]).

    Returns:
        8 rows x 12 columns of 0/1 ints, same shape convention as the
        existing icon bitmaps in main.py's icon_cache.
    """
    cx, cy = centroid
    frame_width = frame_width or 1
    frame_height = frame_height or 1

    col = _clamp(int(cx / frame_width * GRID_COLS), 0, GRID_COLS - 1)
    row = _clamp(int(cy / frame_height * GRID_ROWS), 0, GRID_ROWS - 1)

    bitmap = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
    for dr in (0, 1):
        for dc in (0, 1):
            r = min(row + dr, GRID_ROWS - 1)
            c = min(col + dc, GRID_COLS - 1)
            bitmap[r][c] = 1

    return bitmap
