"""
track_store.py -- in-memory per-track history of POST /track/analyze results,
the time series the fall-detection logic will read (nothing consumes it yet
beyond the dashboard's /user/tracks routes), plus the latest skeleton-annotated
frame per track so the dashboard can show live pose video.

Same module-level, lock-guarded ring-buffer style as events.py /
recognize_activity.py: one deque per live track_id, capped at
QONCLAVE_TRACK_HISTORY_MAX samples (default 150 = ~40s at the edge's 4 Hz
pose cadence).

Two independent caps keep memory bounded without needing the edge to tell the
hub when a track dies (it never does -- prune() exists for a caller that
doesn't yet):
  * per track:   HISTORY_MAX samples (deque maxlen)
  * across tracks: TRACKS_MAX least-recently-updated tracks are evicted,
    so a long session that churns through hundreds of track_ids can't grow
    without limit.

The annotated frames are held in memory (not read back off disk) so the MJPEG
stream works even with QONCLAVE_TRACK_FRAMES_ENABLED=0 -- i.e. you can watch
live pose without any imagery being persisted, which is the privacy-preserving
default the cascade wants.
"""

from __future__ import annotations

import itertools
import os
import threading
from collections import deque
from typing import Any

from . import transport

HISTORY_MAX = int(os.environ.get("QONCLAVE_TRACK_HISTORY_MAX", "150"))
TRACKS_MAX = int(os.environ.get("QONCLAVE_TRACK_MAX", "50"))

_lock = threading.Lock()
# Frame waiters block on this; it shares _lock so a record_frame() publish and
# the history append it accompanies are seen together.
_frame_cv = threading.Condition(_lock)

_tracks: "dict[int, deque[dict[str, Any]]]" = {}
_latest_frame: "dict[int, str]" = {}          # filename on disk (when retention is on)
_frame_bytes: "dict[int, bytes]" = {}         # latest annotated JPEG, in memory
_frame_seq: "dict[int, int]" = {}             # bumped on every new frame
_updated_at: "dict[int, int]" = {}            # monotonic-ish ordering for LRU eviction
_seq = itertools.count(1)


def _touch_locked(track_id: int) -> None:
    """Mark a track as most-recently-used and evict the coldest tracks beyond
    TRACKS_MAX. Caller must hold _lock."""
    _updated_at[track_id] = next(_seq)
    if len(_updated_at) <= TRACKS_MAX:
        return
    for stale in sorted(_updated_at, key=_updated_at.get)[:len(_updated_at) - TRACKS_MAX]:
        _updated_at.pop(stale, None)
        _tracks.pop(stale, None)
        _latest_frame.pop(stale, None)
        _frame_bytes.pop(stale, None)
        _frame_seq.pop(stale, None)


def record(track_id: int, face_result: "dict | None", pose_result: "dict | None",
           frame_name: "str | None" = None,
           analysis: "dict | None" = None) -> None:
    """Append one /track/analyze result to the track's ring buffer.

    face_result / pose_result are the endpoint's per-analyzer sub-objects
    (either may be None when that analyzer wasn't requested); frame_name is
    the annotated frame written to disk for this sample, if any.
    """
    sample = {
        "ts": transport.now_iso(),
        "identity": (face_result or {}).get("identity"),
        "status": (face_result or {}).get("status"),
        "keypoints": (pose_result or {}).get("keypoints"),
        "mean_score": (pose_result or {}).get("mean_score"),
        "pose_status": (pose_result or {}).get("status"),
        "analysis": analysis,
    }
    with _lock:
        buf = _tracks.get(track_id)
        if buf is None:
            buf = _tracks[track_id] = deque(maxlen=HISTORY_MAX)
        buf.append(sample)
        if frame_name:
            _latest_frame[track_id] = frame_name
        _touch_locked(track_id)


def record_frame(track_id: int, jpeg_bytes: bytes) -> None:
    """Publish a new skeleton-annotated frame for a track, waking every open
    MJPEG stream watching it."""
    with _frame_cv:
        _frame_bytes[track_id] = jpeg_bytes
        _frame_seq[track_id] = next(_seq)
        _touch_locked(track_id)
        _frame_cv.notify_all()


def latest_frame_bytes(track_id: int) -> "bytes | None":
    """The track's newest annotated JPEG, or None if it has no pose frame."""
    with _lock:
        return _frame_bytes.get(track_id)


def wait_for_frame(track_id: int, last_seq: int, timeout: float):
    """Block until the track has a frame newer than last_seq.

    Returns (jpeg_bytes, seq), or (None, last_seq) if nothing new arrived
    within `timeout` -- the MJPEG generator uses that to decide whether to
    keep the connection open or close a dead track's stream.
    """
    with _frame_cv:
        if _frame_seq.get(track_id, -1) != last_seq and track_id in _frame_bytes:
            return _frame_bytes[track_id], _frame_seq[track_id]
        _frame_cv.wait(timeout)
        seq = _frame_seq.get(track_id, -1)
        if seq != last_seq and track_id in _frame_bytes:
            return _frame_bytes[track_id], seq
    return None, last_seq


def history(track_id: int) -> list:
    """All retained samples for one track, oldest first ([] if unknown)."""
    with _lock:
        buf = _tracks.get(track_id)
        return list(buf) if buf else []


def latest_frame(track_id: int) -> "str | None":
    """Filename of the track's latest annotated frame on disk, if retention
    is enabled and one was written."""
    with _lock:
        return _latest_frame.get(track_id)


def snapshot() -> dict:
    """{track_id: {identity, status, latest_pose, history_len}} for the
    dashboard.

    identity/status come from the most recent sample that actually carried a
    face result, NOT from the newest sample outright: once a track is `known`
    the edge stops sampling its face and sends pose only, so the newest sample
    is nearly always face-less. Reporting that as `identity: null` would make
    an identified person look unidentified. latest_pose is the newest sample's
    pose fields.
    """
    with _lock:
        out = {}
        for tid, buf in _tracks.items():
            last = buf[-1] if buf else {}
            identity, status = None, None
            # A successful known match is sticky for this numeric track ID.
            # Later weak crops can legitimately report unknown/no_face when a
            # person turns or falls, but must not erase the established name.
            known_sample = next(
                (sample for sample in reversed(buf)
                 if sample.get("status") == "known" and sample.get("identity")),
                None,
            )
            if known_sample is not None:
                identity, status = known_sample.get("identity"), "known"
            else:
                for sample in reversed(buf):
                    if sample.get("status"):
                        identity, status = sample.get("identity"), sample.get("status")
                        break
            out[tid] = {
                "identity": identity,
                "status": status,
                "latest_pose": {
                    "status": last.get("pose_status"),
                    "mean_score": last.get("mean_score"),
                    "ts": last.get("ts"),
                },
                "history_len": len(buf),
                "has_frame": tid in _frame_bytes or tid in _latest_frame,
                "posture": next(
                    (sample.get("analysis") for sample in reversed(buf)
                     if sample.get("analysis") is not None), None),
            }
        return out


def prune(active_ids) -> list:
    """Drop every track not in active_ids; returns the dropped ids so the
    caller can also remove their annotated frames."""
    active = set(active_ids)
    with _frame_cv:
        dropped = [tid for tid in _tracks if tid not in active]
        for tid in dropped:
            del _tracks[tid]
            _latest_frame.pop(tid, None)
            _frame_bytes.pop(tid, None)
            _frame_seq.pop(tid, None)
            _updated_at.pop(tid, None)
        _frame_cv.notify_all()  # let streams for dropped tracks notice and close
    return dropped


def clear() -> None:
    """Test helper: reset all state."""
    with _frame_cv:
        _tracks.clear()
        _latest_frame.clear()
        _frame_bytes.clear()
        _frame_seq.clear()
        _updated_at.clear()
        _frame_cv.notify_all()
