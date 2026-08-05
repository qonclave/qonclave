"""
test_track_store.py — the per-track keypoint history.

This buffer is what fall logic will read, so its two structural properties
matter more than they look: it is bounded PER TRACK (one person standing still
must not evict everyone else's history), and identity is sticky (a later frame
showing the back of someone's head is not evidence they became a stranger).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework import track_store  # noqa: E402

KNOWN = {"identity": "Jogendra", "confidence": 0.93, "status": "known"}
UNKNOWN = {"identity": "unknown", "confidence": 0.2, "status": "unknown"}
POSE_OK = {"status": "ok", "keypoints": [[1.0, 2.0, 0.9]] * 17, "mean_score": 0.8}
POSE_NONE = {"status": "no_pose", "keypoints": None, "mean_score": 0.05}


@pytest.fixture(autouse=True)
def _clean():
    track_store.clear()
    yield
    track_store.clear()


def test_records_and_reads_back():
    track_store.record(1, KNOWN, POSE_OK)
    hist = track_store.history(1)
    assert len(hist) == 1
    assert hist[0]["identity"] == "Jogendra"
    assert len(hist[0]["keypoints"]) == 17


def test_history_is_bounded_per_track(monkeypatch):
    monkeypatch.setattr(track_store, "HISTORY_MAX", 5)
    track_store.clear()
    for _ in range(20):
        track_store.record(1, KNOWN, POSE_OK)
    assert len(track_store.history(1)) == 5


def test_one_busy_track_does_not_evict_another():
    """The reason the buffer is per track rather than global."""
    track_store.record(2, KNOWN, POSE_OK)
    for _ in range(500):
        track_store.record(1, KNOWN, POSE_OK)
    assert len(track_store.history(2)) == 1


def test_identity_is_sticky():
    """Matches the edge's IdentityMap rule. A person who turns away has not
    become someone else."""
    track_store.record(1, KNOWN, POSE_OK)
    track_store.record(1, UNKNOWN, POSE_OK)
    snap = track_store.snapshot()
    assert snap[1]["identity"] == "Jogendra"
    assert snap[1]["status"] == "known"


def test_a_sample_with_neither_analyzer_is_still_recorded():
    """'We looked and saw nothing' is information. A gap and a negative are
    different things to anything reading this as a time series."""
    track_store.record(1, None, None)
    hist = track_store.history(1)
    assert len(hist) == 1
    assert hist[0]["keypoints"] is None


def test_no_pose_is_distinguishable_from_a_gap():
    track_store.record(1, KNOWN, POSE_NONE)
    assert track_store.history(1)[0]["pose_status"] == "no_pose"


def test_snapshot_shape():
    track_store.record(3, KNOWN, POSE_OK, frame_name="track_3.jpg")
    row = track_store.snapshot()[3]
    assert set(row) == {"identity", "status", "history_len", "last_seen", "latest_pose"}
    assert row["history_len"] == 1
    assert row["latest_pose"]["status"] == "ok"


def test_latest_returns_the_newest_sample():
    track_store.record(1, KNOWN, POSE_OK)
    track_store.record(1, KNOWN, POSE_NONE)
    assert track_store.latest(1)["pose_status"] == "no_pose"


def test_prune_drops_tracks_the_edge_no_longer_reports():
    """The edge owns track lifetime — it assigns the ids and knows when someone
    left. The hub ageing entries out on its own timer would make the two
    disagree about who is present."""
    for track_id in (1, 2, 3):
        track_store.record(track_id, KNOWN, POSE_OK)
    dropped = track_store.prune({1, 3})
    assert sorted(dropped) == [2]
    assert set(track_store.snapshot()) == {1, 3}


def test_prune_with_nothing_active_clears_everything():
    track_store.record(1, KNOWN, POSE_OK)
    track_store.prune(set())
    assert track_store.snapshot() == {}


def test_latest_frame_tracks_the_last_written_name():
    track_store.record(1, KNOWN, POSE_OK, frame_name="track_1.jpg")
    assert track_store.latest_frame(1) == "track_1.jpg"


def test_unknown_track_reads_are_empty_not_errors():
    assert track_store.history(999) == []
    assert track_store.latest(999) is None
    assert track_store.latest_frame(999) is None
