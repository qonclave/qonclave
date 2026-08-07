"""
test_events.py — the operator UI's ring buffer.

Small, but two of these encode behaviour that is easy to lose in a refactor and
invisible when it breaks: the dashboard keeps showing the last frame across a
payload-free event, and the buffer bounds itself.
"""

from __future__ import annotations

from qonclave.hub.events import EventStore


def test_newest_first():
    s = EventStore()
    s.record({"event_id": "a"})
    s.record({"event_id": "b"})
    items, _ = s.recent()
    assert [e["event_id"] for e in items] == ["b", "a"]


def test_buffer_is_bounded():
    s = EventStore(maxlen=3)
    for i in range(10):
        s.record({"event_id": str(i)})
    items, _ = s.recent()
    assert len(items) == 3
    assert [e["event_id"] for e in items] == ["9", "8", "7"]


def test_latest_frame_survives_a_payload_free_event():
    """A sensor reading arriving after a camera frame must not blank the
    dashboard's image. Deriving 'latest frame' from the newest event would."""
    s = EventStore()
    s.record({"event_id": "with-frame"}, frame_name="f1.jpg")
    s.record({"event_id": "no-frame"})
    assert s.latest_frame_name() == "f1.jpg"
    _, latest = s.recent()
    assert latest == "f1.jpg"


def test_latest_node_id_reads_either_vocabulary():
    """Events reach the store already normalized, but a legacy record written
    straight through must not silently lose its device."""
    s = EventStore()
    s.record({"device_id": "legacy-01"})
    assert s.latest_node_id() == "legacy-01"
    s.record({"source_node_id": "spec-01"})
    assert s.latest_node_id() == "spec-01"


def test_node_id_persists_when_a_later_event_lacks_one():
    s = EventStore()
    s.record({"source_node_id": "unoq-01"})
    s.record({"event_id": "anonymous"})
    assert s.latest_node_id() == "unoq-01"


def test_limit_caps_the_read():
    s = EventStore()
    for i in range(5):
        s.record({"event_id": str(i)})
    items, _ = s.recent(limit=2)
    assert len(items) == 2


def test_stores_are_independent():
    """The reason this is a class: two instances must not share state."""
    a, b = EventStore(), EventStore()
    a.record({"event_id": "only-in-a"})
    assert b.recent()[0] == []


def test_clear_resets_everything():
    s = EventStore()
    s.record({"source_node_id": "n"}, frame_name="f.jpg")
    s.clear()
    assert s.recent()[0] == []
    assert s.latest_frame_name() is None
    assert s.latest_node_id() is None
