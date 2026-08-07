#!/usr/bin/env python3
"""
test_recognize_activity.py — unit tests for framework/recognize_activity.py's
in-memory ring buffer (record/recent/get_image), independent of Flask.

Run from the repo root:
    python hub/tests/test_recognize_activity.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from framework import recognize_activity  # noqa: E402


def test_record_and_recent_round_trip():
    recognize_activity.record(4, "Jogendra", 0.93, "known", 12.3, b"fake-jpeg-bytes", source_ip="10.0.0.1")
    items = recognize_activity.recent(1)
    assert items[0]["track_id"] == 4
    assert items[0]["identity"] == "Jogendra"
    assert items[0]["status"] == "known"
    assert items[0]["source_ip"] == "10.0.0.1"
    assert "image" not in items[0]  # metadata only, no raw bytes in recent()


def test_get_image_returns_the_recorded_bytes():
    recognize_activity.record(7, "unknown", 0.2, "unknown", 5.0, b"crop-bytes-7")
    entry_id = recognize_activity.recent(1)[0]["id"]
    assert recognize_activity.get_image(entry_id) == b"crop-bytes-7"


def test_unknown_entry_id_returns_none():
    assert recognize_activity.get_image(999999999) is None


def test_recent_is_newest_first():
    recognize_activity.record(101, "a", 0.1, "unknown", 1.0, b"a")
    recognize_activity.record(102, "b", 0.1, "unknown", 1.0, b"b")
    items = recognize_activity.recent(2)
    assert items[0]["track_id"] == 102
    assert items[1]["track_id"] == 101


def test_buffer_is_capped_at_max_entries():
    n = recognize_activity.MAX_ENTRIES + 5
    for i in range(n):
        recognize_activity.record(i, "x", 0.1, "unknown", 1.0, f"crop-{i}".encode())
    items = recognize_activity.recent(n + 10)
    assert len(items) == recognize_activity.MAX_ENTRIES
    # oldest 5 of this loop's appends were evicted; the buffer holds the tail.
    assert items[0]["track_id"] == n - 1  # newest
    assert items[-1]["track_id"] == 5     # oldest survivor


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
