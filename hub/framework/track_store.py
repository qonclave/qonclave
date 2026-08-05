"""
track_store.py — per-track history of analysis results.

Mirrors `framework/events.py`'s shape: module-level, lock-guarded ring buffers,
ephemeral. This is the time series fall logic will read; nothing consumes it yet
beyond the dashboard, which is deliberate — building the buffer before the
consumer means the consumer can be written against real recorded data rather
than against a guess about what should have been recorded.

Bounded per track, not globally. A single person standing in frame for an hour
must not evict the history of everyone else, which a shared buffer would do.
"""

from __future__ import annotations

import collections
import os
import threading
import time

# 150 samples at the default 4 Hz is about 40 seconds of history — long enough
# to see a fall and its aftermath, short enough that a busy scene stays cheap.
HISTORY_MAX = int(os.environ.get("QONCLAVE_TRACK_HISTORY_MAX", "150"))

_tracks: dict[int, collections.deque] = {}
_meta: dict[int, dict] = {}
_lock = threading.Lock()


def record(track_id: int, face_result: dict | None, pose_result: dict | None,
           frame_name: str | None = None) -> None:
    """Append one analysis sample for a track.

    Both analyzers are optional: a request may have asked for only one, or one
    may have been unavailable. A sample with neither is still recorded, because
    "we looked and saw nothing" is information a time series needs — a gap and a
    negative are different things.
    """
    face_result = face_result or {}
    pose_result = pose_result or {}

    sample = {
        "ts": time.time(),
        "keypoints": pose_result.get("keypoints"),
        "mean_score": pose_result.get("mean_score"),
        "pose_status": pose_result.get("status"),
        "identity": face_result.get("identity"),
        "status": face_result.get("status"),
    }

    with _lock:
        if track_id not in _tracks:
            _tracks[track_id] = collections.deque(maxlen=HISTORY_MAX)
            _meta[track_id] = {}
        _tracks[track_id].append(sample)

        meta = _meta[track_id]
        meta["last_seen"] = sample["ts"]
        # Identity is sticky, matching the edge's IdentityMap rule: a track that
        # was recognised once stays recognised, because a later frame showing the
        # back of someone's head is not evidence they became a different person.
        if face_result.get("status") == "known" and face_result.get("identity"):
            meta["identity"] = face_result["identity"]
            meta["status"] = "known"
        elif "status" not in meta and face_result.get("status"):
            meta["status"] = face_result["status"]
        if frame_name:
            meta["frame"] = frame_name


def history(track_id: int) -> list[dict]:
    with _lock:
        return list(_tracks.get(track_id, ()))


def latest(track_id: int) -> dict | None:
    with _lock:
        buf = _tracks.get(track_id)
        return dict(buf[-1]) if buf else None


def latest_frame(track_id: int) -> str | None:
    with _lock:
        return _meta.get(track_id, {}).get("frame")


def snapshot() -> dict[int, dict]:
    """One row per live track, for the dashboard."""
    with _lock:
        out = {}
        for track_id, buf in _tracks.items():
            meta = _meta.get(track_id, {})
            last = buf[-1] if buf else {}
            out[track_id] = {
                "identity": meta.get("identity"),
                "status": meta.get("status"),
                "history_len": len(buf),
                "last_seen": meta.get("last_seen"),
                "latest_pose": {
                    "status": last.get("pose_status"),
                    "mean_score": last.get("mean_score"),
                    "keypoints": last.get("keypoints"),
                },
            }
        return out


def prune(active_ids) -> list[int]:
    """Drop tracks the edge no longer reports. Returns the ids dropped.

    The edge owns track lifetime — it assigns the ids and knows when a person
    left. The hub following that rather than ageing entries out on its own
    timer is what keeps the two from disagreeing about who is present.
    """
    active = set(active_ids)
    with _lock:
        dropped = [t for t in _tracks if t not in active]
        for track_id in dropped:
            _tracks.pop(track_id, None)
            _meta.pop(track_id, None)
        return dropped


def clear() -> None:
    with _lock:
        _tracks.clear()
        _meta.clear()
