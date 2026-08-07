# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
follow_target_selector.py -- decides which person track the robot follows.

Selection order (docs/follow_known_person_plan.md):
    visible known person with the lowest hub priority number (1 = highest)
    -> previously selected known person, held for a grace period while missing
    -> longest-established visible unknown track
    -> no target

The selector owns ALL grace state; motor commands must only ever come from
tracks in the current frame. select() returns a dict whose "track" key is
either a track dict from this frame's person_tracks or None (during grace /
no target), so a stale bounding box structurally cannot produce a turn.

One select() call per detection callback = one grace tick. Stdlib-only, no
logging or threading: it runs on the detection-callback thread only, and
main.py owns logging/UI emission.

IdentityMap pruning hazard: detection runs ~1.5 Hz, so the default 10 grace
frames span ~6.7 s -- longer than IdentityMap's 5 s inactive_grace_sec. The
selector therefore retains its own copy of the target's identity/priority; if
the *same* track_id reappears mid-grace after its IdentityMap entry was
pruned, following resumes from the retained copy instead of demanding a fresh
recognition. A track recreated with a NEW id gets no such shortcut: it only
becomes a known candidate once the identity snapshot says "known" again.
"""

from __future__ import annotations

DEFAULT_PRIORITY = 100

FOLLOWING = "following"
KNOWN_TARGET_MISSING = "known_target_missing"
FALLBACK_UNKNOWN = "fallback_unknown"
NO_TARGET = "no_target"


class FollowTargetSelector:
    def __init__(self, grace_frames: int = 10):
        self.grace_frames = grace_frames
        # Retained known target: {"track_id", "identity", "priority",
        # "missing_frames"}. Only ever a KNOWN person -- unknown fallback
        # targets get no grace and no retained state.
        self._target: dict | None = None
        self._last_state: str | None = None

    def select(self, person_tracks, identity_snapshot, priority_map) -> dict:
        """Pick this frame's follow target.

        person_tracks: this frame's tracks (each with "track_id" and
            "frames_tracked" at minimum).
        identity_snapshot: {track_id: {"name", "status", ...}} from IdentityMap.
        priority_map: {identity slug: int} from the hub (PriorityMapClient).
        """
        target = self._target

        # 1. Known candidates: visible tracks recognized as known, plus the
        # same-id-resume case (retained target's id back, entry pruned).
        candidates = []  # (track_id, identity, priority, track)
        for track in person_tracks:
            tid = track["track_id"]
            entry = identity_snapshot.get(tid)
            if entry and entry.get("status") == "known":
                name = entry["name"]
            elif target and tid == target["track_id"]:
                name = target["identity"]
            else:
                continue
            candidates.append(
                (tid, name, priority_map.get(name, DEFAULT_PRIORITY), track))

        # 2. Best known: lowest priority number, then stickiness to the
        # current target, then most-established, then lowest id.
        if candidates:
            current_id = target["track_id"] if target else None
            tid, name, prio, track = min(
                candidates,
                key=lambda c: (c[2], 0 if c[0] == current_id else 1,
                               -c[3]["frames_tracked"], c[0]))
            reason = self._following_reason(tid, prio, candidates)
            self._target = {"track_id": tid, "identity": name,
                            "priority": prio, "missing_frames": 0}
            return self._result(FOLLOWING, tid, name, "known", prio, reason,
                                0, track)

        # 3. Known target missing: hold for the grace period, even when
        # unknowns are visible and even on empty frames (both still tick).
        if target and target["missing_frames"] < self.grace_frames:
            target["missing_frames"] += 1
            return self._result(
                KNOWN_TARGET_MISSING, target["track_id"], target["identity"],
                "known", target["priority"], "grace_hold",
                target["missing_frames"], None)

        # 4./5. Grace over (or never had a known target): forget it and fall
        # back to the longest-established unknown track, if any.
        expired = target is not None
        self._target = None

        if person_tracks:
            track = min(person_tracks,
                        key=lambda t: (-t["frames_tracked"], t["track_id"]))
            tid = track["track_id"]
            status = identity_snapshot.get(tid, {}).get("status")
            reason = ("grace_expired_fallback" if expired
                      else "longest_established_unknown")
            return self._result(FALLBACK_UNKNOWN, tid, None, status, None,
                                reason, 0, track)

        return self._result(NO_TARGET, None, None, None, None,
                            "no_visible_tracks", 0, None)

    def _following_reason(self, tid, prio, candidates) -> str:
        target = self._target
        if target is None:
            return ("preempted_unknown" if self._last_state == FALLBACK_UNKNOWN
                    else "highest_priority_known")
        if tid == target["track_id"]:
            if target["missing_frames"] > 0:
                return "target_reacquired"
            if any(c[0] != tid and c[2] == prio for c in candidates):
                return "kept_current_equal_priority"
            return "highest_priority_known"
        if target["missing_frames"] == 0 and prio < target["priority"]:
            return "preempted_lower_priority"
        return "highest_priority_known"

    def _result(self, state, track_id, identity, status, priority, reason,
                missing_frames, track) -> dict:
        self._last_state = state
        return {
            "track_id": track_id,
            "identity": identity,
            "status": status,
            "priority": priority,
            "state": state,
            "reason": reason,
            "missing_frames": missing_frames,
            "grace_frames": self.grace_frames,
            "track": track,
        }
