# SPDX-License-Identifier: MPL-2.0

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from analysis_client import AnalysisClient  # noqa: E402


def _client(face_interval_sec=1.0, pose_interval_sec=0.25, analyzers=("face", "pose")):
    return AnalysisClient(get_hub_base_url=lambda: "http://unused",
                          face_interval_sec=face_interval_sec,
                          pose_interval_sec=pose_interval_sec,
                          analyzers=analyzers)


# --- the 8 cases ported from test_recognition_client.py ---------------------

def test_first_sight_of_a_track_samples_immediately():
    c = _client()
    assert c.analyzers_due(4, is_known=False, now=0.0) == {"face", "pose"}


def test_known_track_is_never_face_sampled():
    c = _client()
    # face never fires for a known track; pose keeps going (see below).
    assert "face" not in c.analyzers_due(4, is_known=True, now=0.0)


def test_respects_sample_interval_for_unknown_track():
    c = _client(face_interval_sec=1.0, pose_interval_sec=1.0)
    assert c.analyzers_due(7, is_known=False, now=0.0) == {"face", "pose"}
    c.claim(7, {"face", "pose"}, now=0.0)
    c.release(7)  # simulate the send completing, without a real HTTP call
    assert c.analyzers_due(7, is_known=False, now=0.5) == set()
    assert c.analyzers_due(7, is_known=False, now=1.0) == {"face", "pose"}


def test_in_flight_track_is_not_resampled():
    c = _client()
    with c._lock:
        c._in_flight.add(4)
    assert c.analyzers_due(4, is_known=False, now=100.0) == set()


def test_forget_clears_sampling_state():
    c = _client()
    with c._lock:
        c._last_sent_at[4] = {"face": 0.0, "pose": 0.0}
        c._in_flight.add(4)
    c.forget(4)
    assert c.analyzers_due(4, is_known=False, now=0.1) == {"face", "pose"}


def test_claim_marks_in_flight_and_stamps_last_sent():
    c = _client(face_interval_sec=1.0, pose_interval_sec=1.0)
    c.claim(4, {"face", "pose"}, now=10.0)
    assert c.analyzers_due(4, is_known=False, now=10.1) == set()  # still in flight
    c.release(4)
    assert c.analyzers_due(4, is_known=False, now=10.1) == set()  # released, interval not elapsed
    assert c.analyzers_due(4, is_known=False, now=11.0) == {"face", "pose"}


def test_claim_before_crop_work_prevents_a_second_frame_from_also_sampling():
    # Mirrors main.py's real sequence: analyzers_due() then claim() happen
    # back-to-back on the hot path, *before* any crop/encode work -- so a
    # second detection callback for the same track, arriving while that work
    # is still in progress, must see it as already claimed.
    c = _client()
    assert c.analyzers_due(4, is_known=False, now=0.0) == {"face", "pose"}
    c.claim(4, {"face", "pose"}, now=0.0)
    assert c.analyzers_due(4, is_known=False, now=0.01) == set()


def test_release_does_not_clear_last_sent_at():
    c = _client(face_interval_sec=1.0, pose_interval_sec=1.0)
    c.claim(4, {"face", "pose"}, now=0.0)
    c.release(4)  # crop was rejected -- still counts as "just tried"
    assert c.analyzers_due(4, is_known=False, now=0.5) == set()


# --- new multi-analyzer cases ------------------------------------------------

def test_pose_stays_due_after_track_goes_known():
    # The face-specific "stop entirely once known" rule must not starve pose:
    # fall detection needs a continuous time series for as long as the person
    # is tracked.
    c = _client(face_interval_sec=1.0, pose_interval_sec=0.25)
    c.claim(4, {"face", "pose"}, now=0.0)
    c.release(4)
    assert c.analyzers_due(4, is_known=True, now=0.25) == {"pose"}
    assert c.analyzers_due(4, is_known=True, now=100.0) == {"pose"}


def test_one_in_flight_request_blocks_both_analyzers():
    # _in_flight is per-track, not per-analyzer: one request carries all due
    # analyzers, so nothing else fires until it lands.
    c = _client(face_interval_sec=1.0, pose_interval_sec=0.25)
    c.claim(4, {"pose"}, now=0.0)  # pose-only claim (track already known)
    assert c.analyzers_due(4, is_known=False, now=50.0) == set()
    c.release(4)
    assert c.analyzers_due(4, is_known=False, now=50.0) == {"face", "pose"}


def test_per_analyzer_intervals_are_independent():
    # face at 1.0s and pose at 0.25s: between ticks only pose comes due, and
    # a pose-only claim must not push face's stamp forward.
    c = _client(face_interval_sec=1.0, pose_interval_sec=0.25)
    c.claim(4, {"face", "pose"}, now=0.0)
    c.release(4)
    assert c.analyzers_due(4, is_known=False, now=0.25) == {"pose"}
    c.claim(4, {"pose"}, now=0.25)
    c.release(4)
    assert c.analyzers_due(4, is_known=False, now=0.5) == {"pose"}
    assert c.analyzers_due(4, is_known=False, now=1.0) == {"face", "pose"}


def test_disabled_analyzer_is_never_due():
    c = _client(analyzers=("face",))
    assert c.analyzers_due(4, is_known=False, now=0.0) == {"face"}
    assert c.analyzers_due(4, is_known=True, now=0.0) == set()


def test_pose_request_carries_resolved_identity():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"track_id": 4, "pose": {"status": "ok"}}

    sent = {}

    def post(_url, **kwargs):
        sent.update(kwargs["data"])
        return Response()

    client = _client()
    client.claim(4, {"pose"}, now=0.0)
    with patch("analysis_client.requests.post", side_effect=post):
        client.send_claimed(4, b"jpeg", {"pose"}, lambda *_: None,
                            known_identity="bob")
    assert sent["known_identity"] == "bob"
    assert sent["analyzers"] == "pose"


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
