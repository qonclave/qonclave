# SPDX-License-Identifier: MPL-2.0

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from priority_sync import PriorityMapClient  # noqa: E402


class _Response:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            raise ValueError("malformed JSON")
        return self._payload


def _client():
    return PriorityMapClient(get_hub_base_url=lambda: "http://hub:8000")


def test_snapshot_is_empty_before_first_fetch():
    assert _client().snapshot() == {}


def test_successful_fetch_replaces_map_and_hits_the_right_url():
    seen = {}

    def get(url, timeout):
        seen["url"] = url
        seen["timeout"] = timeout
        return _Response({"people": [{"identity": "jogendra", "priority": 1},
                                     {"identity": "alice", "priority": 2}]})

    c = _client()
    with patch("priority_sync.requests.get", side_effect=get):
        assert c.refresh_now() is True
    assert seen["url"] == "http://hub:8000/user/known-person-priorities"
    assert c.snapshot() == {"jogendra": 1, "alice": 2}


def test_malformed_entries_are_skipped_not_fatal():
    payload = {"people": [
        {"identity": "jogendra", "priority": 1},
        {"identity": "bob"},                       # missing priority
        {"priority": 3},                           # missing identity
        {"identity": "carol", "priority": "abc"},  # unparseable priority
        "not-a-dict",
        {"identity": "dave", "priority": "4"},     # numeric string is fine
    ]}
    c = _client()
    with patch("priority_sync.requests.get",
               return_value=_Response(payload)):
        assert c.refresh_now() is True
    assert c.snapshot() == {"jogendra": 1, "dave": 4}


def test_malformed_json_keeps_old_map():
    c = _client()
    with patch("priority_sync.requests.get",
               return_value=_Response({"people": [{"identity": "jogendra", "priority": 1}]})):
        c.refresh_now()
    with patch("priority_sync.requests.get", return_value=_Response(None)):
        assert c.refresh_now() is False
    assert c.snapshot() == {"jogendra": 1}


def test_http_error_keeps_old_map():
    c = _client()
    with patch("priority_sync.requests.get",
               return_value=_Response({"people": [{"identity": "jogendra", "priority": 1}]})):
        c.refresh_now()
    with patch("priority_sync.requests.get", return_value=_Response({}, status=500)):
        assert c.refresh_now() is False
    assert c.snapshot() == {"jogendra": 1}


def test_connection_error_keeps_old_map():
    # Spec case 10: the priority API being unavailable never clears the cache.
    c = _client()
    with patch("priority_sync.requests.get",
               return_value=_Response({"people": [{"identity": "jogendra", "priority": 1}]})):
        c.refresh_now()
    with patch("priority_sync.requests.get", side_effect=OSError("connection refused")):
        assert c.refresh_now() is False
    assert c.snapshot() == {"jogendra": 1}


def test_updated_payload_replaces_map():
    # Edge half of spec case 14: a dashboard priority change lands on refresh.
    c = _client()
    with patch("priority_sync.requests.get",
               return_value=_Response({"people": [{"identity": "jogendra", "priority": 100}]})):
        c.refresh_now()
    with patch("priority_sync.requests.get",
               return_value=_Response({"people": [{"identity": "jogendra", "priority": 1}]})):
        assert c.refresh_now() is True
    assert c.snapshot() == {"jogendra": 1}


def test_snapshot_returns_a_copy():
    c = _client()
    with patch("priority_sync.requests.get",
               return_value=_Response({"people": [{"identity": "jogendra", "priority": 1}]})):
        c.refresh_now()
    snap = c.snapshot()
    snap["jogendra"] = 99
    assert c.snapshot() == {"jogendra": 1}


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
