# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
identity_map.py -- per-track identity bookkeeping for the hub's face
recognition results (POST /recognize), fed by RecognitionClient/main.py.

Implements a "never lose information" rule: known > unknown > no_face >
unidentified/error/unavailable, and a track's status can only move up that
ladder, never down.

    known response: always overwrites (sticky-final)
    no entry yet: always set (first response ever)
    otherwise: only replace if the new status outranks the current one

This fixes a real bug: a track's *first* sample sometimes misses the face
entirely (turned away, motion blur, bad crop) and gets recorded as no_face;
without ranking, that first-response-wins rule then blocks every later
"unknown" from ever correcting it, even once the hub clearly finds and
fails to match a real face on subsequent samples (confirmed on-device: a
track sat on "No face" for 10+ seconds while the hub logged "unknown" for
every sample after the first). Known is still the only status that's fully
sticky -- once identified, later unknown/no_face samples (the person turns
away, a brief occlusion) never erase it.
"""

from __future__ import annotations

import threading

UNIDENTIFIED = "unidentified"
KNOWN = "known"
UNKNOWN = "unknown"
NO_FACE = "no_face"

_DEFAULT_ENTRY = {"name": UNIDENTIFIED, "confidence": 0.0, "status": UNIDENTIFIED}

# Higher rank = more information gained; a status can only move up this
# ladder, never down. Anything unrecognized (shouldn't happen) ranks like
# unidentified, so it can still be upgraded rather than getting stuck.
_STATUS_RANK = {
    UNIDENTIFIED: 0,
    "error": 0,
    "unavailable": 0,
    NO_FACE: 1,
    UNKNOWN: 2,
    KNOWN: 3,
}


class IdentityMap:
    """Thread-safe: recognition responses arrive on background HTTP threads
    (see recognition_client.py) while main.py reads/prunes it from the
    detection callback thread."""

    def __init__(self):
        self._entries: dict[int, dict] = {}
        self._lock = threading.Lock()

    def merge(self, track_id: int, result: dict) -> None:
        """Apply one hub /recognize response to track_id -- see module
        docstring for the full upgrade-only rule."""
        entry = {
            "name": result.get("identity", UNKNOWN),
            "confidence": result.get("confidence", 0.0) or 0.0,
            "status": result.get("status", UNKNOWN),
        }
        with self._lock:
            if entry["status"] == KNOWN:
                self._entries[track_id] = entry
                return
            current = self._entries.get(track_id)
            if current is None:
                self._entries[track_id] = entry
            elif _STATUS_RANK.get(entry["status"], 0) > _STATUS_RANK.get(current["status"], 0):
                self._entries[track_id] = entry

    def get(self, track_id: int) -> dict:
        with self._lock:
            return dict(self._entries.get(track_id, _DEFAULT_ENTRY))

    def is_known(self, track_id: int) -> bool:
        return self.get(track_id)["status"] == KNOWN

    def prune(self, active_track_ids) -> list[int]:
        """Drop entries for tracks that are no longer active (the tracker
        dropped them). Returns the dropped track_ids so callers can also
        clean up per-track state elsewhere (e.g. saved crop files)."""
        active = set(active_track_ids)
        with self._lock:
            dropped = [tid for tid in self._entries if tid not in active]
            for tid in dropped:
                del self._entries[tid]
        return dropped

    def snapshot(self) -> dict:
        """{track_id: {"name", "confidence", "status"}}, for display/logging."""
        with self._lock:
            return {tid: dict(entry) for tid, entry in self._entries.items()}
