"""
test_discovery_registry.py — the generic sighting ledger.

Distinct from placement's peers/health tests: this pins the two rules that make
registry.py a registry rather than a log — one physical node is one row
(anonymous rows are absorbed once the node introduces itself), and rows age
through online -> idle -> offline instead of being evicted. Ported from
hub/tests/test_device_registry.py, which this module now backs.
"""

from __future__ import annotations

import time as _time

import pytest

from qonclave.discovery import registry


@pytest.fixture(autouse=True)
def fresh_registry():
    registry.clear()
    yield
    registry.clear()


# --- record / merge ----------------------------------------------------------

def test_sighting_with_no_identifier_is_dropped():
    registry.record(node_id="", ip=None, source="event")
    assert registry.snapshot() == []


def test_identified_node_absorbs_prior_anonymous_row():
    """A crop arrives before the node has ever named itself; the later
    identified sighting must fold both into one row, keeping the earlier
    first_seen and the sighting count."""
    registry.record(ip="10.0.0.5", source="track")
    registry.record(node_id="unoq-01", ip="10.0.0.5", source="event")

    rows = registry.snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row["node_id"] == "unoq-01"
    assert row["ip"] == "10.0.0.5"
    assert row["sightings"] == 2
    assert row["sources"] == ["event", "track"]


def test_anonymous_sighting_credits_known_node_at_same_ip():
    registry.record(node_id="unoq-01", ip="10.0.0.5", source="event")
    registry.record(ip="10.0.0.5", source="track")

    rows = registry.snapshot()
    assert len(rows) == 1
    assert rows[0]["node_id"] == "unoq-01"
    assert rows[0]["sightings"] == 2


def test_distinct_nodes_stay_distinct():
    registry.record(node_id="unoq-01", ip="10.0.0.5", source="event")
    registry.record(node_id="unoq-02", ip="10.0.0.6", source="event")
    registry.record(ip="10.0.0.7", source="discovery")
    assert len(registry.snapshot()) == 3


# --- MQTT topic parsing ------------------------------------------------------

@pytest.mark.parametrize("topic,expected", [
    ("qonclave/status/unoq-01", "unoq-01"),   # spec layout
    ("qonclave/unoq-01/status", "unoq-01"),   # pre-spec layout
])
def test_status_topics_yield_a_node(topic, expected):
    registry.record_mqtt_topic(topic)
    rows = registry.snapshot()
    assert len(rows) == 1
    assert rows[0]["node_id"] == expected
    assert rows[0]["sources"] == ["mqtt"]


@pytest.mark.parametrize("topic", [
    "qonclave/commands/unoq-01",       # command, not status
    "qonclave/status",                 # no node id
    "other/status/unoq-01",            # not our namespace
    "qonclave/status/unoq-01/extra",   # wrong depth
])
def test_non_status_topics_are_ignored(topic):
    registry.record_mqtt_topic(topic)
    assert registry.snapshot() == []


# --- state ageing --------------------------------------------------------------

def test_rows_age_out_instead_of_disappearing(monkeypatch):
    now = _time.time()
    registry.record(node_id="unoq-01", source="event")

    monkeypatch.setattr(registry.time, "time", lambda: now + registry.ONLINE_S + 1)
    assert registry.snapshot()[0]["state"] == "idle"

    monkeypatch.setattr(registry.time, "time", lambda: now + registry.OFFLINE_S + 1)
    rows = registry.snapshot()
    assert rows[0]["state"] == "offline"
    assert len(rows) == 1  # quiet nodes are shown, not evicted


# --- distance measurement seam ------------------------------------------------

def test_probe_targets_excludes_ip_less_entries():
    registry.record(node_id="has-ip", ip="10.0.0.5", source="event")
    registry.record(node_id="no-ip", source="event")

    targets = registry.probe_targets()
    ips = {ip for _key, ip in targets}
    assert ips == {"10.0.0.5"}


def test_probe_targets_excludes_offline_entries(monkeypatch):
    now = _time.time()
    registry.record(node_id="stale", ip="10.0.0.9", source="event")

    monkeypatch.setattr(registry.time, "time", lambda: now + registry.OFFLINE_S + 1)

    assert registry.probe_targets() == []


def test_record_rtt_stores_against_the_right_entry():
    registry.record(node_id="unoq-01", ip="10.0.0.5", source="event")
    (key, _ip), = registry.probe_targets()

    registry.record_rtt(key, 12.3)

    assert registry.snapshot()[0]["rtt_ms"] == 12.3


def test_record_rtt_is_a_noop_for_a_vanished_entry():
    registry.record_rtt("ip:10.0.0.99", 5.0)  # never recorded; must not raise
    assert registry.snapshot() == []


def test_rtt_survives_absorption_into_an_identified_row():
    registry.record(ip="10.0.0.5", source="track")
    (key, _ip), = registry.probe_targets()
    registry.record_rtt(key, 7.5)

    registry.record(node_id="unoq-01", ip="10.0.0.5", source="event")

    assert registry.snapshot()[0]["rtt_ms"] == 7.5
