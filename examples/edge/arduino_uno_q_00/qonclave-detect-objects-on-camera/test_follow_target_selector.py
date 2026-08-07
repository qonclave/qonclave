# SPDX-License-Identifier: MPL-2.0

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from follow_target_selector import (  # noqa: E402
    DEFAULT_PRIORITY,
    FALLBACK_UNKNOWN,
    FOLLOWING,
    KNOWN_TARGET_MISSING,
    NO_TARGET,
    FollowTargetSelector,
)


def _track(tid, frames=1, box=(50, 50, 150, 150)):
    return {"track_id": tid, "frames_tracked": frames,
            "centroid": (100, 100), "bounding_box_xyxy": box}


def _known(name):
    return {"name": name, "confidence": 0.9, "status": "known"}


def _unknown():
    return {"name": "unknown", "confidence": 0.2, "status": "unknown"}


def _unidentified():
    return {"name": "unidentified", "confidence": 0.0, "status": "unidentified"}


# --- spec case 1: a known person beats any unknown --------------------------

def test_known_person_beats_longer_established_unknown():
    s = FollowTargetSelector()
    sel = s.select([_track(7, frames=50), _track(3, frames=2)],
                   {7: _unknown(), 3: _known("jogendra")},
                   {"jogendra": 1})
    assert sel["state"] == FOLLOWING
    assert sel["track_id"] == 3
    assert sel["identity"] == "jogendra"
    assert sel["priority"] == 1
    assert sel["reason"] == "highest_priority_known"
    assert sel["track"]["track_id"] == 3


# --- spec case 2: lowest priority number wins -------------------------------

def test_lowest_priority_number_wins_among_knowns():
    s = FollowTargetSelector()
    sel = s.select([_track(1, frames=9), _track(2, frames=9)],
                   {1: _known("alice"), 2: _known("bob")},
                   {"alice": 2, "bob": 1})
    assert sel["track_id"] == 2
    assert sel["identity"] == "bob"
    assert sel["priority"] == 1


# --- spec case 3: equal priorities -> stickiness, then frames, then id ------

def test_equal_priority_keeps_current_target():
    s = FollowTargetSelector()
    assert s.select([_track(1)], {1: _known("alice")}, {"alice": 5})["track_id"] == 1
    # bob appears with the same priority and MORE frames: alice stays current.
    sel = s.select([_track(1, frames=2), _track(2, frames=50)],
                   {1: _known("alice"), 2: _known("bob")},
                   {"alice": 5, "bob": 5})
    assert sel["track_id"] == 1
    assert sel["reason"] == "kept_current_equal_priority"


def test_equal_priority_no_current_prefers_more_frames_then_lower_id():
    s = FollowTargetSelector()
    sel = s.select([_track(1, frames=2), _track(2, frames=50)],
                   {1: _known("alice"), 2: _known("bob")},
                   {"alice": 5, "bob": 5})
    assert sel["track_id"] == 2  # more frames wins

    s2 = FollowTargetSelector()
    sel2 = s2.select([_track(4, frames=7), _track(2, frames=7)],
                     {4: _known("alice"), 2: _known("bob")},
                     {"alice": 5, "bob": 5})
    assert sel2["track_id"] == 2  # equal frames: lower track_id wins


# --- larger bounding box wins ties (closest/most prominent person) ----------

def test_larger_box_wins_among_equal_priority_knowns():
    s = FollowTargetSelector()
    sel = s.select(
        [_track(1, frames=50, box=(50, 50, 150, 150)),   # smaller box, more frames
         _track(2, frames=2, box=(0, 0, 200, 200))],      # larger box, fewer frames
        {1: _known("alice"), 2: _known("bob")},
        {"alice": 5, "bob": 5})
    assert sel["track_id"] == 2  # box size outranks frames_tracked


def test_stickiness_beats_larger_box_among_equal_priority_knowns():
    s = FollowTargetSelector()
    s.select([_track(1, box=(50, 50, 150, 150))], {1: _known("alice")}, {"alice": 5})
    # bob appears with the same priority and a much larger box: alice stays
    # current -- box size is not allowed to cause flicker off an active target.
    sel = s.select(
        [_track(1, box=(50, 50, 150, 150)), _track(2, box=(0, 0, 300, 300))],
        {1: _known("alice"), 2: _known("bob")},
        {"alice": 5, "bob": 5})
    assert sel["track_id"] == 1
    assert sel["reason"] == "kept_current_equal_priority"


# --- spec case 4: no knowns -> longest-established unknown ------------------

def test_unknown_fallback_by_frames_then_id():
    s = FollowTargetSelector()
    sel = s.select([_track(7, frames=50), _track(9, frames=3)],
                   {7: _unknown(), 9: _unknown()}, {})
    assert sel["state"] == FALLBACK_UNKNOWN
    assert sel["track_id"] == 7
    assert sel["priority"] is None
    assert sel["reason"] == "longest_established_unknown"
    assert sel["track"]["track_id"] == 7

    s2 = FollowTargetSelector()
    sel2 = s2.select([_track(7, frames=5), _track(2, frames=5)],
                     {7: _unknown(), 2: _unknown()}, {})
    assert sel2["track_id"] == 2  # tie on frames: lower id


def test_larger_box_wins_among_unknowns():
    s = FollowTargetSelector()
    sel = s.select(
        [_track(7, frames=50, box=(50, 50, 150, 150)),   # smaller box, more frames
         _track(9, frames=2, box=(0, 0, 200, 200))],      # larger box, fewer frames
        {7: _unknown(), 9: _unknown()}, {})
    assert sel["state"] == FALLBACK_UNKNOWN
    assert sel["track_id"] == 9  # box size outranks frames_tracked


# --- spec cases 5 + 12: grace holds; no stale track for motor commands ------

def test_grace_holds_while_unknown_visible_and_track_is_none():
    s = FollowTargetSelector(grace_frames=10)
    s.select([_track(3)], {3: _known("jogendra")}, {"jogendra": 1})
    for n in range(1, 10):
        sel = s.select([_track(7, frames=50)], {7: _unknown()}, {"jogendra": 1})
        assert sel["state"] == KNOWN_TARGET_MISSING, n
        assert sel["track_id"] == 3
        assert sel["identity"] == "jogendra"
        assert sel["priority"] == 1
        assert sel["missing_frames"] == n
        assert sel["reason"] == "grace_hold"
        assert sel["track"] is None  # spec case 12: never a stale box


# --- spec case 6: target returns during grace -> resume ---------------------

def test_target_reacquired_during_grace():
    s = FollowTargetSelector()
    s.select([_track(3)], {3: _known("jogendra")}, {"jogendra": 1})
    s.select([_track(7)], {7: _unknown()}, {"jogendra": 1})
    sel = s.select([_track(3, frames=4), _track(7, frames=9)],
                   {3: _known("jogendra"), 7: _unknown()}, {"jogendra": 1})
    assert sel["state"] == FOLLOWING
    assert sel["track_id"] == 3
    assert sel["reason"] == "target_reacquired"
    assert sel["track"]["track_id"] == 3


def test_same_id_resumes_after_identity_map_pruned_entry():
    # IdentityMap's 5s inactive grace is shorter than 10 detection frames at
    # ~1.5 Hz: the same track_id can come back with its identity entry gone.
    # The selector's retained copy carries the identity across that gap.
    s = FollowTargetSelector()
    s.select([_track(3)], {3: _known("jogendra")}, {"jogendra": 1})
    s.select([], {}, {"jogendra": 1})
    s.select([], {}, {"jogendra": 1})
    sel = s.select([_track(3, frames=1)], {3: _unidentified()}, {"jogendra": 1})
    assert sel["state"] == FOLLOWING
    assert sel["track_id"] == 3
    assert sel["identity"] == "jogendra"
    assert sel["priority"] == 1
    assert sel["reason"] == "target_reacquired"
    assert sel["track"]["track_id"] == 3


# --- spec case 7: grace expires exactly at grace_frames + 1 -----------------

def test_grace_expires_exactly_at_grace_plus_one():
    s = FollowTargetSelector(grace_frames=3)
    s.select([_track(3)], {3: _known("jogendra")}, {"jogendra": 1})
    unknown_frame = [_track(7, frames=50)]
    for n in (1, 2, 3):
        sel = s.select(unknown_frame, {7: _unknown()}, {"jogendra": 1})
        assert sel["state"] == KNOWN_TARGET_MISSING
        assert sel["missing_frames"] == n
    sel = s.select(unknown_frame, {7: _unknown()}, {"jogendra": 1})
    assert sel["state"] == FALLBACK_UNKNOWN
    assert sel["track_id"] == 7
    assert sel["reason"] == "grace_expired_fallback"
    # Next frame is plain fallback, not "expired" again.
    sel = s.select(unknown_frame, {7: _unknown()}, {"jogendra": 1})
    assert sel["reason"] == "longest_established_unknown"


# --- spec case 8: another known appears mid-grace ---------------------------

def test_other_known_selected_mid_grace():
    s = FollowTargetSelector()
    s.select([_track(3)], {3: _known("jogendra")}, {"jogendra": 1, "bob": 2})
    s.select([_track(7)], {7: _unknown()}, {"jogendra": 1, "bob": 2})
    # bob (a visible known) wins over the missing target, whatever priority.
    sel = s.select([_track(5), _track(7)],
                   {5: _known("bob"), 7: _unknown()},
                   {"jogendra": 1, "bob": 2})
    assert sel["state"] == FOLLOWING
    assert sel["track_id"] == 5
    assert sel["identity"] == "bob"
    # ...and jogendra's return then preempts bob by priority.
    sel = s.select([_track(3), _track(5)],
                   {3: _known("jogendra"), 5: _known("bob")},
                   {"jogendra": 1, "bob": 2})
    assert sel["track_id"] == 3


# --- spec case 9: preemption ------------------------------------------------

def test_known_preempts_unknown_immediately():
    s = FollowTargetSelector()
    sel = s.select([_track(7, frames=50)], {7: _unknown()}, {})
    assert sel["state"] == FALLBACK_UNKNOWN
    sel = s.select([_track(7, frames=51), _track(3, frames=1)],
                   {7: _unknown(), 3: _known("jogendra")}, {"jogendra": 1})
    assert sel["state"] == FOLLOWING
    assert sel["track_id"] == 3
    assert sel["reason"] == "preempted_unknown"


def test_higher_priority_known_preempts_lower():
    s = FollowTargetSelector()
    s.select([_track(5)], {5: _known("bob")}, {"bob": 2, "jogendra": 1})
    sel = s.select([_track(5, frames=2), _track(3, frames=1)],
                   {5: _known("bob"), 3: _known("jogendra")},
                   {"bob": 2, "jogendra": 1})
    assert sel["track_id"] == 3
    assert sel["identity"] == "jogendra"
    assert sel["reason"] == "preempted_lower_priority"


# --- spec case 10: identity missing from the map defaults to 100 ------------

def test_missing_identity_defaults_to_100():
    s = FollowTargetSelector()
    sel = s.select([_track(1)], {1: _known("stranger")}, {})
    assert sel["priority"] == DEFAULT_PRIORITY
    # An explicitly prioritized person beats the default.
    sel = s.select([_track(1), _track(2)],
                   {1: _known("stranger"), 2: _known("jogendra")},
                   {"jogendra": 99})
    assert sel["track_id"] == 2


# --- spec case 11: recreated track must re-confirm before being followed ----

def test_recreated_track_needs_recognition_then_follows():
    s = FollowTargetSelector(grace_frames=5)
    s.select([_track(3)], {3: _known("jogendra")}, {"jogendra": 1})
    # New id 9, not yet recognized: grace holds instead of following it.
    sel = s.select([_track(9, frames=1)], {9: _unidentified()}, {"jogendra": 1})
    assert sel["state"] == KNOWN_TARGET_MISSING
    assert sel["track"] is None
    # Recognition confirms the new id: now it is followed.
    sel = s.select([_track(9, frames=2)], {9: _known("jogendra")}, {"jogendra": 1})
    assert sel["state"] == FOLLOWING
    assert sel["track_id"] == 9
    assert sel["track"]["track_id"] == 9


# --- spec case 12: empty frames still tick the grace counter ----------------

def test_empty_frames_tick_grace_then_no_target():
    s = FollowTargetSelector(grace_frames=2)
    s.select([_track(3)], {3: _known("jogendra")}, {"jogendra": 1})
    sel = s.select([], {}, {"jogendra": 1})
    assert sel["state"] == KNOWN_TARGET_MISSING
    assert sel["missing_frames"] == 1
    assert sel["track"] is None
    sel = s.select([], {}, {"jogendra": 1})
    assert sel["missing_frames"] == 2
    sel = s.select([], {}, {"jogendra": 1})
    assert sel["state"] == NO_TARGET
    assert sel["track_id"] is None
    assert sel["track"] is None


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
