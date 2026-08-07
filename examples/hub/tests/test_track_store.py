#!/usr/bin/env python3
"""
test_track_store.py — ring-buffer contract for framework/track_store.py:
per-track history cap, prune, snapshot shape, latest-frame bookkeeping.

Run from the repo root:
    python hub/tests/test_track_store.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
sys.path.insert(0, HUB_DIR)

from framework import track_store  # noqa: E402

_FACE = {"identity": "Priya", "confidence": 0.93, "status": "known"}
_POSE = {"status": "ok", "keypoints": [[1.0, 2.0, 0.9]] * 17, "mean_score": 0.71}


def test_record_and_history_roundtrip():
    track_store.clear()
    track_store.record(4, _FACE, _POSE, frame_name="track_4.jpg")
    hist = track_store.history(4)
    assert len(hist) == 1
    sample = hist[0]
    assert sample["identity"] == "Priya"
    assert sample["status"] == "known"
    assert sample["pose_status"] == "ok"
    assert sample["mean_score"] == 0.71
    assert sample["keypoints"] == _POSE["keypoints"]
    assert "ts" in sample
    assert track_store.latest_frame(4) == "track_4.jpg"


def test_partial_results_are_recorded_as_none():
    track_store.clear()
    track_store.record(4, None, _POSE)          # pose-only tick (known person)
    track_store.record(4, _FACE, None)          # face-only tick
    hist = track_store.history(4)
    assert hist[0]["identity"] is None and hist[0]["pose_status"] == "ok"
    assert hist[1]["identity"] == "Priya" and hist[1]["pose_status"] is None


def test_history_is_capped_at_maxlen():
    track_store.clear()
    for _ in range(track_store.HISTORY_MAX + 25):
        track_store.record(4, None, _POSE)
    assert len(track_store.history(4)) == track_store.HISTORY_MAX


def test_unknown_track_has_empty_history_and_no_frame():
    track_store.clear()
    assert track_store.history(99) == []
    assert track_store.latest_frame(99) is None


def test_snapshot_shape():
    track_store.clear()
    track_store.record(4, _FACE, _POSE, frame_name="track_4.jpg")
    track_store.record(5, None, {"status": "no_pose", "keypoints": None, "mean_score": 0.05})
    snap = track_store.snapshot()
    assert set(snap) == {4, 5}
    assert snap[4]["identity"] == "Priya"
    assert snap[4]["status"] == "known"
    assert snap[4]["history_len"] == 1
    assert snap[4]["has_frame"] is True
    assert snap[4]["latest_pose"]["status"] == "ok"
    assert snap[4]["latest_pose"]["mean_score"] == 0.71
    assert snap[5]["identity"] is None
    assert snap[5]["has_frame"] is False
    assert snap[5]["latest_pose"]["status"] == "no_pose"


def test_snapshot_keeps_identity_through_pose_only_samples():
    # The real steady state: face resolves once, then the edge sends pose
    # only. The newest sample carries no face result, but the person is still
    # identified -- snapshot() must not report them as unidentified.
    track_store.clear()
    track_store.record(4, _FACE, _POSE)
    for _ in range(10):
        track_store.record(4, None, _POSE)
    snap = track_store.snapshot()[4]
    assert snap["identity"] == "Priya"
    assert snap["status"] == "known"
    assert snap["latest_pose"]["status"] == "ok"   # still the newest sample's


def test_snapshot_identity_is_the_most_recent_face_result():
    track_store.clear()
    track_store.record(4, {"identity": "no_face", "confidence": 0.0, "status": "no_face"}, None)
    track_store.record(4, _FACE, None)
    track_store.record(4, None, _POSE)
    snap = track_store.snapshot()[4]
    assert (snap["identity"], snap["status"]) == ("Priya", "known")


def test_snapshot_known_identity_survives_later_weak_face_results():
    track_store.clear()
    track_store.record(6, _FACE, _POSE)
    track_store.record(
        6, {"identity": "unknown", "confidence": 0.12, "status": "unknown"}, _POSE)
    track_store.record(
        6, {"identity": "no_face", "confidence": 0.0, "status": "no_face"}, _POSE)

    snap = track_store.snapshot()[6]
    assert snap["identity"] == "Priya"
    assert snap["status"] == "known"


def test_record_frame_is_served_from_memory():
    track_store.clear()
    assert track_store.latest_frame_bytes(4) is None
    track_store.record_frame(4, b"jpeg-one")
    assert track_store.latest_frame_bytes(4) == b"jpeg-one"
    track_store.record_frame(4, b"jpeg-two")
    assert track_store.latest_frame_bytes(4) == b"jpeg-two"


def test_wait_for_frame_returns_immediately_when_newer_exists():
    track_store.clear()
    track_store.record_frame(4, b"jpeg-one")
    frame, seq = track_store.wait_for_frame(4, last_seq=-1, timeout=0.1)
    assert frame == b"jpeg-one"
    # Same seq back in: nothing newer, so it blocks then reports no change.
    frame2, seq2 = track_store.wait_for_frame(4, last_seq=seq, timeout=0.05)
    assert frame2 is None and seq2 == seq


def test_wait_for_frame_wakes_on_a_new_frame():
    import threading as _t
    track_store.clear()
    track_store.record_frame(4, b"first")
    _, seq = track_store.wait_for_frame(4, last_seq=-1, timeout=0.1)

    _t.Timer(0.05, lambda: track_store.record_frame(4, b"second")).start()
    frame, new_seq = track_store.wait_for_frame(4, last_seq=seq, timeout=2.0)
    assert frame == b"second"
    assert new_seq != seq


def test_track_count_is_capped_by_lru_eviction():
    track_store.clear()
    for tid in range(track_store.TRACKS_MAX + 10):
        track_store.record(tid, None, _POSE)
    snap = track_store.snapshot()
    assert len(snap) == track_store.TRACKS_MAX
    # The oldest ids were evicted; the newest survive.
    assert 0 not in snap
    assert (track_store.TRACKS_MAX + 9) in snap


def test_has_frame_is_true_for_memory_only_frames():
    track_store.clear()
    track_store.record(4, None, _POSE)          # no frame_name (retention off)
    track_store.record_frame(4, b"jpeg")
    assert track_store.snapshot()[4]["has_frame"] is True


def test_prune_drops_inactive_and_reports_them():
    track_store.clear()
    track_store.record(4, _FACE, _POSE, frame_name="track_4.jpg")
    track_store.record(5, None, _POSE)
    track_store.record(6, None, _POSE)
    track_store.record_frame(4, b"jpeg")
    dropped = track_store.prune(active_ids={5})
    assert sorted(dropped) == [4, 6]
    assert set(track_store.snapshot()) == {5}
    assert track_store.latest_frame(4) is None        # frame bookkeeping pruned too
    assert track_store.latest_frame_bytes(4) is None  # and the in-memory frame


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
