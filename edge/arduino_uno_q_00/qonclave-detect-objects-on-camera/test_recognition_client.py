# SPDX-License-Identifier: MPL-2.0

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from recognition_client import RecognitionClient  # noqa: E402


def _client(sample_interval_sec=1.0):
    return RecognitionClient(get_hub_base_url=lambda: "http://unused", sample_interval_sec=sample_interval_sec)


def test_first_sight_of_a_track_samples_immediately():
    c = _client()
    assert c.should_sample(4, is_known=False, now=0.0)


def test_known_track_is_never_sampled():
    c = _client()
    assert not c.should_sample(4, is_known=True, now=0.0)


def test_respects_sample_interval_for_unknown_track():
    c = _client(sample_interval_sec=1.0)
    assert c.should_sample(7, is_known=False, now=0.0)
    c.claim(7, now=0.0)
    c.release(7)  # simulate the send completing, without a real HTTP call
    assert not c.should_sample(7, is_known=False, now=0.5)
    assert c.should_sample(7, is_known=False, now=1.0)


def test_in_flight_track_is_not_resampled():
    c = _client()
    with c._lock:
        c._in_flight.add(4)
    assert not c.should_sample(4, is_known=False, now=100.0)


def test_forget_clears_sampling_state():
    c = _client()
    with c._lock:
        c._last_sent_at[4] = 0.0
        c._in_flight.add(4)
    c.forget(4)
    assert c.should_sample(4, is_known=False, now=0.1)


def test_claim_marks_in_flight_and_stamps_last_sent():
    c = _client()
    c.claim(4, now=10.0)
    assert not c.should_sample(4, is_known=False, now=10.1)  # still in flight
    c.release(4)
    assert not c.should_sample(4, is_known=False, now=10.1)  # released, but interval not elapsed
    assert c.should_sample(4, is_known=False, now=11.0)


def test_claim_before_crop_work_prevents_a_second_frame_from_also_sampling():
    # Mirrors main.py's real sequence: should_sample() then claim() happen
    # back-to-back on the hot path, *before* any crop/encode work -- so a
    # second detection callback for the same track, arriving while that work
    # is still in progress, must see it as already claimed.
    c = _client()
    assert c.should_sample(4, is_known=False, now=0.0)
    c.claim(4, now=0.0)
    assert not c.should_sample(4, is_known=False, now=0.01)


def test_release_does_not_clear_last_sent_at():
    c = _client(sample_interval_sec=1.0)
    c.claim(4, now=0.0)
    c.release(4)  # crop was rejected -- still counts as "just tried"
    assert not c.should_sample(4, is_known=False, now=0.5)


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
