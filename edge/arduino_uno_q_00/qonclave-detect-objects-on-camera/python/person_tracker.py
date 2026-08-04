# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
person_tracker.py -- lightweight cross-frame identity tracking for detected
persons, using nothing but the bounding boxes VideoObjectDetection already
emits (see app_bricks/video_objectdetection: detection_details["bounding_box_xyxy"]).

Each call to VideoObjectDetection's on_detect_all callback hands us an
independent, per-frame dict of detections -- there is no notion of "this is
the same person as last frame". PersonTracker.update() closes that gap with a
simple greedy nearest-centroid tracker (the same approach popularized by
dlib/imutils' CentroidTracker): match this frame's person boxes against the
previous frame's tracked centroids by Euclidean distance, capped at
max_distance so a new person entering frame never "steals" a nearby existing
track. It assigns a persistent track_id and estimates a coarse compass
direction (8-way) from recent centroid history, so a future camera-rotation
feature has both pan (left/right) and tilt (up/down) signals to act on.

This is intentionally dependency-free (no numpy/scipy) to match this app's
existing footprint (requirements.txt: requests, python-dotenv, paho-mqtt).
"""

from __future__ import annotations

import math
from collections import deque


def _centroid(bounding_box_xyxy) -> tuple[float, float]:
    x1, y1, x2, y2 = bounding_box_xyxy
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# 8-way compass bucket, atan2(dy, dx) in image coordinates (y grows downward).
_DIRECTIONS = [
    (0.0, "right"),
    (45.0, "down-right"),
    (90.0, "down"),
    (135.0, "down-left"),
    (180.0, "left"),
    (-135.0, "up-left"),
    (-90.0, "up"),
    (-45.0, "up-right"),
]


def _bucket_direction(dx: float, dy: float) -> str:
    angle = math.degrees(math.atan2(dy, dx))
    best_label = "right"
    best_delta = 361.0
    for bucket_angle, label in _DIRECTIONS:
        delta = abs(angle - bucket_angle)
        delta = min(delta, 360.0 - delta)
        if delta < best_delta:
            best_delta = delta
            best_label = label
    return best_label


class _Track:
    __slots__ = ("id", "centroid", "bounding_box_xyxy", "disappeared", "history", "frames_tracked")

    def __init__(self, track_id: int, centroid: tuple[float, float], bounding_box_xyxy, history_len: int):
        self.id = track_id
        self.centroid = centroid
        self.bounding_box_xyxy = bounding_box_xyxy
        self.disappeared = 0
        self.history: deque[tuple[float, float]] = deque([centroid], maxlen=history_len)
        self.frames_tracked = 1

    def mark_matched(self, centroid: tuple[float, float], bounding_box_xyxy):
        self.centroid = centroid
        self.bounding_box_xyxy = bounding_box_xyxy
        self.disappeared = 0
        self.history.append(centroid)
        self.frames_tracked += 1

    def direction(self, min_movement_px: float) -> str:
        if len(self.history) < 2:
            return "stationary"
        oldest = self.history[0]
        newest = self.history[-1]
        dx = newest[0] - oldest[0]
        dy = newest[1] - oldest[1]
        if math.hypot(dx, dy) < min_movement_px:
            return "stationary"
        return _bucket_direction(dx, dy)


class PersonTracker:
    """Assigns persistent IDs to detected persons across frames and reports
    a coarse movement direction, based only on each frame's bounding boxes.

    Usage:
        tracker = PersonTracker()
        tracks = tracker.update(detections.get("person", []))
        # tracks: same detection dicts, each enriched with track_id/centroid/
        # direction/dx/dy/frames_tracked.
    """

    def __init__(
        self,
        max_disappeared: int = 10,
        max_distance: float = 150.0,
        direction_history: int = 5,
        min_movement_px: float = 10.0,
    ):
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance
        self.direction_history = max(2, direction_history)
        self.min_movement_px = min_movement_px

        self._tracks: dict[int, _Track] = {}
        self._next_id = 1

    def update(self, person_detections: list[dict]) -> list[dict]:
        """Match this frame's person detections against existing tracks.

        Args:
            person_detections: this frame's `detections.get("person", [])`,
                each a dict with at least a `bounding_box_xyxy` key.

        Returns:
            The same detection dicts (in the same order), each enriched with
            `track_id`, `centroid`, `direction`, `dx`, `dy`, `frames_tracked`.
        """
        centroids = [_centroid(d["bounding_box_xyxy"]) for d in person_detections]

        track_ids = list(self._tracks.keys())
        unmatched_detections = set(range(len(person_detections)))
        matched_tracks = set()

        # Greedy nearest-centroid matching: repeatedly pick the closest
        # remaining (track, detection) pair under max_distance. Fine for the
        # handful of people we expect in frame -- no need for a full
        # assignment solver (e.g. Hungarian algorithm) at this scale.
        pairs = []
        for tid in track_ids:
            for di in unmatched_detections:
                dist = _distance(self._tracks[tid].centroid, centroids[di])
                if dist <= self.max_distance:
                    pairs.append((dist, tid, di))
        pairs.sort(key=lambda p: p[0])

        di_to_tid: dict[int, int] = {}
        for dist, tid, di in pairs:
            if tid in matched_tracks or di not in unmatched_detections:
                continue
            self._tracks[tid].mark_matched(centroids[di], person_detections[di]["bounding_box_xyxy"])
            matched_tracks.add(tid)
            unmatched_detections.discard(di)
            di_to_tid[di] = tid

        # Unmatched existing tracks: aging out.
        for tid in track_ids:
            if tid not in matched_tracks:
                track = self._tracks[tid]
                track.disappeared += 1
                if track.disappeared > self.max_disappeared:
                    del self._tracks[tid]

        # Unmatched detections: brand new tracks.
        for di in list(unmatched_detections):
            new_id = self._next_id
            self._next_id += 1
            self._tracks[new_id] = _Track(
                new_id, centroids[di], person_detections[di]["bounding_box_xyxy"], self.direction_history
            )
            di_to_tid[di] = new_id

        results = []
        for di, detection in enumerate(person_detections):
            tid = di_to_tid[di]
            track = self._tracks[tid]
            oldest = track.history[0]
            newest = track.history[-1]
            enriched = dict(detection)
            enriched["track_id"] = tid
            enriched["centroid"] = track.centroid
            enriched["direction"] = track.direction(self.min_movement_px)
            enriched["dx"] = newest[0] - oldest[0]
            enriched["dy"] = newest[1] - oldest[1]
            enriched["frames_tracked"] = track.frames_tracked
            results.append(enriched)
        return results
