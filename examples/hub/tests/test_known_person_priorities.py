#!/usr/bin/env python3
"""
test_known_person_priorities.py — spec case 13: known-person follow-priority
validation, persistence, defaults, atomic updates, and the two framework
routes that expose them (GET/PUT /user/known-person-priorities).

Store half runs against a tempfile path with an injected known_names, no
face-ID models needed. Route half goes through Flask's test client with stub
backends (model: test_track_analyze_endpoint.py).

Run from the repo root:
    python hub/tests/test_known_person_priorities.py
"""

import json
import os
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
sys.path.insert(0, HUB_DIR)

# Keep endpoint tests from writing annotated frames into hub/track_frames/
# (read at import time by framework.server).
os.environ["QONCLAVE_TRACK_FRAMES_ENABLED"] = "0"

from apps.security import known_person_priorities as kpp  # noqa: E402
from apps.security.known_person_priorities import KnownPersonPriorityStore  # noqa: E402
from framework import track_store  # noqa: E402
from framework.server import create_app  # noqa: E402


def _tmp_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)  # store must cope with the file not existing yet
    return path


def _store(known=("jogendra", "alice"), path=None):
    return KnownPersonPriorityStore(path=path or _tmp_path(),
                                    known_names=lambda: list(known))


# --- store: defaults ---------------------------------------------------------

def test_enrolled_people_default_to_100():
    s = _store()
    assert s.list_people() == [{"identity": "alice", "priority": 100},
                               {"identity": "jogendra", "priority": 100}]


def test_no_known_names_source_means_empty_roster():
    s = KnownPersonPriorityStore(path=_tmp_path(), known_names=None)
    assert s.list_people() == []
    assert s.set_priority("jogendra", 1) is None


def test_list_sorts_by_priority_then_identity():
    s = _store(known=("jogendra", "alice", "bob"))
    s.set_priority("jogendra", 1)
    assert s.list_people() == [{"identity": "jogendra", "priority": 1},
                               {"identity": "alice", "priority": 100},
                               {"identity": "bob", "priority": 100}]


# --- store: validation -------------------------------------------------------

def test_validation_rejects_bad_priorities():
    s = _store()
    for bad in (0, -1, "abc", 1.5, True, None):
        try:
            s.set_priority("jogendra", bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")


def test_validation_accepts_int_and_numeric_string():
    s = _store()
    assert s.set_priority("jogendra", 1) == {"identity": "jogendra", "priority": 1}
    assert s.set_priority("alice", "2") == {"identity": "alice", "priority": 2}


def test_equal_priorities_are_allowed():
    s = _store()
    s.set_priority("jogendra", 3)
    s.set_priority("alice", 3)
    assert [p["priority"] for p in s.list_people()] == [3, 3]


# --- store: persistence + atomicity ------------------------------------------

def test_priorities_persist_across_instances():
    path = _tmp_path()
    _store(path=path).set_priority("jogendra", 1)
    again = _store(path=path)
    assert again.list_people()[0] == {"identity": "jogendra", "priority": 1}
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"jogendra": {"priority": 1}}


def test_save_is_atomic_via_os_replace_and_leaves_no_tmp():
    path = _tmp_path()
    s = _store(path=path)
    replaces = []
    real_replace = os.replace

    def recording_replace(src, dst):
        replaces.append((str(src), str(dst)))
        return real_replace(src, dst)

    with patch.object(kpp.os, "replace", side_effect=recording_replace):
        s.set_priority("jogendra", 1)
    assert replaces, "save must go through os.replace"
    assert replaces[0][1] == path
    assert not os.path.exists(path + ".tmp")
    assert os.path.exists(path)


def test_corrupt_or_missing_file_loads_as_empty():
    path = _tmp_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    s = _store(path=path)
    assert s.list_people()[0]["priority"] == 100  # defaults, no exception


# --- store: stale slugs -------------------------------------------------------

def test_stale_stored_slug_is_omitted_and_rejected():
    path = _tmp_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ghost": {"priority": 1}, "jogendra": {"priority": 2}}, f)
    s = _store(path=path)  # "ghost" is not enrolled
    assert s.list_people() == [{"identity": "jogendra", "priority": 2},
                               {"identity": "alice", "priority": 100}]
    assert s.set_priority("ghost", 1) is None


# --- routes ------------------------------------------------------------------

class _StubVLM:
    def status(self):
        return {"available": False}


class _StubMQTT:
    def status(self):
        return {"available": False}

    def is_available(self):
        return False

    def publish(self, *a, **kw):
        return False

    def publish_command(self, *a, **kw):
        return False


class _StubSMS:
    def status(self):
        return {"available": False}

    def send(self, *a, **kw):
        return False

    def recent_activity(self, limit=50):
        return []


class _HookedPolicy:
    """Policy exposing the two priority hooks, backed by a real store."""

    name = "test"

    def __init__(self, store):
        self._store = store

    def known_person_priorities(self):
        return self._store.list_people()

    def update_known_person_priority(self, slug, priority):
        return self._store.set_priority(slug, priority)


class _HookLessPolicy:
    name = "test"


def _make_app(policy):
    track_store.clear()  # module-level state; isolate each test's app
    return create_app(policy=policy, vlm=_StubVLM(), mqtt=_StubMQTT(),
                      sms=_StubSMS(), static_dir=HERE, face_id=None, pose=None)


def test_get_returns_people_sorted():
    store = _store(known=("jogendra", "alice"))
    store.set_priority("jogendra", 1)
    client = _make_app(_HookedPolicy(store)).test_client()
    body = client.get("/user/known-person-priorities").get_json()
    assert body == {"people": [{"identity": "jogendra", "priority": 1},
                               {"identity": "alice", "priority": 100}]}


def test_put_persists_and_slugifies_the_path_param():
    store = _store(known=("jogendra",))
    client = _make_app(_HookedPolicy(store)).test_client()
    resp = client.put("/user/known-person-priorities/Jogendra",
                      json={"priority": 1})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body == {"ok": True, "identity": "jogendra", "priority": 1}
    assert store.list_people() == [{"identity": "jogendra", "priority": 1}]


def test_put_bad_body_is_400():
    store = _store(known=("jogendra",))
    client = _make_app(_HookedPolicy(store)).test_client()
    for bad in ({"priority": 0}, {"priority": "abc"}, {}, None):
        resp = client.put("/user/known-person-priorities/jogendra", json=bad)
        assert resp.status_code == 400, bad
        assert resp.get_json()["ok"] is False


def test_put_unknown_slug_is_404():
    store = _store(known=("jogendra",))
    client = _make_app(_HookedPolicy(store)).test_client()
    resp = client.put("/user/known-person-priorities/nobody",
                      json={"priority": 1})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "person not enrolled"


def test_hookless_policy_404s_on_both_routes():
    client = _make_app(_HookLessPolicy()).test_client()
    assert client.get("/user/known-person-priorities").status_code == 404
    assert client.put("/user/known-person-priorities/jogendra",
                      json={"priority": 1}).status_code == 404


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
