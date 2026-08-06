"""
test_discovery_announce.py — the UDP announce/probe-respond mechanism.

Real sockets, real threads, on a non-default port so a test run never collides with an
actual qonclave-hub process on the same machine. Ported from a hand debugging session
against hub/framework/discovery.py earlier the same day this migration landed.
"""

from __future__ import annotations

import json
import socket
import time

import pytest

from qonclave.discovery import announce, registry
from qonclave.discovery.backends.udp import UDPAnnounceBackend

TEST_PORT = 18888


@pytest.fixture(autouse=True)
def fresh_registry():
    registry.clear()
    yield
    registry.clear()


# --- UDPAnnounceBackend, in isolation ----------------------------------------

def test_backend_constructs_and_reports_capability():
    backend = UDPAnnounceBackend(port=TEST_PORT, recv_timeout=0.2)
    try:
        assert backend.can_broadcast is True
        assert backend.can_listen is True
    finally:
        backend.reply(b"", ("127.0.0.1", 0))  # no-op; just exercising the guard path


def test_poll_times_out_with_none_when_nothing_arrives():
    backend = UDPAnnounceBackend(port=TEST_PORT, recv_timeout=0.1)
    assert backend.poll() is None


def test_broadcast_and_receive_round_trip():
    """Two independent backends on the same port (SO_REUSEADDR): one broadcasts, the other
    receives it as an ordinary incoming datagram -- exactly how a probe arrives."""
    sender = UDPAnnounceBackend(port=TEST_PORT, recv_timeout=0.5)
    receiver = UDPAnnounceBackend(port=TEST_PORT, recv_timeout=1.0)

    sender.broadcast(b'{"hello":"world"}')
    received = receiver.poll()

    assert received is not None
    data, addr = received
    assert json.loads(data) == {"hello": "world"}


# --- announce.start(), end to end ---------------------------------------------

def _probe(port, payload, timeout=2.0):
    """Send one probe like an edge device does, and wait for a reply."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    try:
        sock.sendto(json.dumps(payload).encode(), ("255.255.255.255", port))
        data, addr = sock.recvfrom(1024)
        return json.loads(data), addr
    finally:
        sock.close()


def test_matching_probe_gets_a_reply():
    announce.start({"service": "qonclave-hub", "port": 8000}, port=TEST_PORT, interval_s=0.3)
    time.sleep(1.2)  # past the thread's own 1.0s startup delay

    reply, _addr = _probe(TEST_PORT, {"probe": "qonclave-hub"})

    assert reply == {"service": "qonclave-hub", "port": 8000}


def test_any_probe_gets_a_reply_regardless_of_service():
    announce.start({"service": "qonclave-hub", "port": 8000}, port=TEST_PORT + 1, interval_s=0.3)
    time.sleep(1.2)

    reply, _addr = _probe(TEST_PORT + 1, {"probe": "any"})

    assert reply == {"service": "qonclave-hub", "port": 8000}


def test_probe_with_a_node_id_is_recorded_identified():
    announce.start({"service": "qonclave-hub", "port": 8000}, port=TEST_PORT + 2, interval_s=0.3)
    time.sleep(1.2)

    _probe(TEST_PORT + 2, {"probe": "any", "node_id": "unoq-01"})
    time.sleep(0.2)  # the responder records just after replying

    rows = registry.snapshot()
    assert len(rows) == 1
    assert rows[0]["node_id"] == "unoq-01"
    assert rows[0]["sources"] == ["discovery"]


def test_probe_without_a_node_id_is_recorded_anonymous():
    announce.start({"service": "qonclave-hub", "port": 8000}, port=TEST_PORT + 3, interval_s=0.3)
    time.sleep(1.2)

    _probe(TEST_PORT + 3, {"probe": "any"})
    time.sleep(0.2)

    rows = registry.snapshot()
    assert len(rows) == 1
    assert rows[0]["node_id"] is None
    assert rows[0]["ip"] is not None
