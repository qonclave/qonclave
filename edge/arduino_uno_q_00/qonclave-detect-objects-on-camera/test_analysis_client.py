# SPDX-License-Identifier: MPL-2.0

"""
test_analysis_client.py — per-analyzer sampling policy.

Replaces test_recognition_client.py. Ports its cases and adds the three the
unified client introduces, each of which is a way the old face-only rule was
wrong for pose:

  * pose stays due after a face goes known
  * one in-flight request blocks both analyzers, not one
  * the two intervals advance independently
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from analysis_client import FACE, POSE, AnalysisClient  # noqa: E402


def sent(c, track_id, analyzers, now):
    """Model a COMPLETED request: claim, then release as send_claimed's finally
    clause does. Claiming alone leaves the track in flight, which correctly
    blocks everything — so an interval test that only claims proves nothing
    about intervals."""
    c.claim(track_id, analyzers, now=now)
    c.release(track_id)


def client(**kw):
    kw.setdefault("get_hub_base_url", lambda: "http://hub:8000")
    kw.setdefault("face_interval_sec", 1.0)
    kw.setdefault("pose_interval_sec", 0.25)
    return AnalysisClient(**kw)


# --- ported from test_recognition_client.py ---------------------------------

def test_first_sighting_is_always_due():
    assert client().analyzers_due(1, is_known=False, now=100.0) == {FACE, POSE}


def test_in_flight_blocks_everything():
    """One request carries every due analyzer, so a second while one is open
    would send a duplicate crop rather than a useful extra sample."""
    c = client()
    c.claim(1, {FACE, POSE}, now=100.0)
    assert c.analyzers_due(1, is_known=False, now=200.0) == set()


def test_release_allows_a_retry():
    c = client()
    c.claim(1, {FACE, POSE}, now=100.0)
    c.release(1)
    assert c.analyzers_due(1, is_known=False, now=101.5) == {FACE, POSE}


def test_forget_clears_all_state():
    c = client()
    c.claim(1, {FACE, POSE}, now=100.0)
    c.forget(1)
    assert c.analyzers_due(1, is_known=False, now=100.0) == {FACE, POSE}


def test_tracks_are_independent():
    c = client()
    c.claim(1, {FACE, POSE}, now=100.0)
    assert c.analyzers_due(2, is_known=False, now=100.0) == {FACE, POSE}


def test_face_stops_once_known():
    """The sticky-known rule, unchanged: identity does not change, so once it
    is resolved there is nothing left to ask."""
    c = client()
    c.claim(1, {FACE}, now=100.0)
    assert FACE not in c.analyzers_due(1, is_known=True, now=200.0)


def test_face_interval_is_respected_while_unknown():
    c = client()
    sent(c, 1, {FACE}, now=100.0)
    assert FACE not in c.analyzers_due(1, is_known=False, now=100.5)
    assert FACE in c.analyzers_due(1, is_known=False, now=101.5)


def test_error_result_has_the_same_shape_as_a_real_one():
    """Callers need no error branch, which only holds if the shape matches."""
    result = AnalysisClient._error_result(4)
    assert result["track_id"] == 4
    assert result["face"]["status"] == "error"
    assert result["pose"]["status"] == "error"
    assert "latency_ms" in result


# --- new: the reasons the face-only rule was wrong for pose ------------------

def test_pose_stays_due_after_the_face_is_known():
    """The heart of the change. Fall detection needs a continuous time series
    for as long as a person is tracked; the old sampler stopped entirely."""
    c = client()
    sent(c, 1, {FACE, POSE}, now=100.0)
    due = c.analyzers_due(1, is_known=True, now=100.5)
    assert due == {POSE}


def test_intervals_advance_independently():
    c = client()
    sent(c, 1, {FACE, POSE}, now=100.0)
    # 0.3s later: pose (0.25s) is due again, face (1.0s) is not.
    assert c.analyzers_due(1, is_known=False, now=100.3) == {POSE}
    # 1.1s later: both.
    assert c.analyzers_due(1, is_known=False, now=101.1) == {FACE, POSE}


def test_claiming_one_analyzer_does_not_stamp_the_other():
    c = client()
    sent(c, 1, {POSE}, now=100.0)
    assert FACE in c.analyzers_due(1, is_known=False, now=100.1)


def test_analyzer_set_is_configurable():
    """A deployment that does not want pose should not pay for it."""
    c = client(analyzers=(FACE,))
    assert c.analyzers_due(1, is_known=False, now=100.0) == {FACE}


def test_pose_only_deployment_ignores_known():
    c = client(analyzers=(POSE,))
    assert c.analyzers_due(1, is_known=True, now=100.0) == {POSE}
