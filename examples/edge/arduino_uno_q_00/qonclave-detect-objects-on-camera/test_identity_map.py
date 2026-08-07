# SPDX-License-Identifier: MPL-2.0

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from identity_map import IdentityMap  # noqa: E402


def _known(name="Jogendra", confidence=0.93):
    return {"identity": name, "confidence": confidence, "status": "known"}


def _unknown(confidence=0.25):
    return {"identity": "unknown", "confidence": confidence, "status": "unknown"}


def _no_face():
    return {"identity": "no_face", "confidence": 0.0, "status": "no_face"}


def test_untracked_id_returns_unidentified_default():
    im = IdentityMap()
    entry = im.get(99)
    assert entry == {"name": "unidentified", "confidence": 0.0, "status": "unidentified"}
    assert not im.is_known(99)


def test_first_response_of_any_status_is_recorded():
    im = IdentityMap()
    im.merge(7, _unknown(0.25))
    assert im.get(7) == {"name": "unknown", "confidence": 0.25, "status": "unknown"}


def test_known_always_overwrites():
    im = IdentityMap()
    im.merge(4, _unknown(0.1))
    im.merge(4, _known("Jogendra", 0.93))
    assert im.get(4) == {"name": "Jogendra", "confidence": 0.93, "status": "known"}
    assert im.is_known(4)


def test_unknown_does_not_overwrite_known():
    im = IdentityMap()
    im.merge(4, _known("Jogendra", 0.93))
    im.merge(4, _unknown(0.2))
    entry = im.get(4)
    assert entry["name"] == "Jogendra"
    assert entry["status"] == "known"


def test_no_face_does_not_erase_known():
    im = IdentityMap()
    im.merge(4, _known("Jogendra", 0.93))
    im.merge(4, _no_face())
    entry = im.get(4)
    assert entry["name"] == "Jogendra"
    assert entry["status"] == "known"


def test_no_face_does_not_overwrite_existing_unknown():
    im = IdentityMap()
    im.merge(7, _unknown(0.25))
    im.merge(7, _no_face())
    entry = im.get(7)
    assert entry["status"] == "unknown"
    assert entry["confidence"] == 0.25


def test_a_later_known_response_can_still_upgrade_an_unknown_track():
    im = IdentityMap()
    im.merge(7, _unknown(0.25))
    im.merge(7, _known("Jogendra", 0.9))
    entry = im.get(7)
    assert entry["name"] == "Jogendra"
    assert entry["status"] == "known"


def test_unknown_upgrades_a_stuck_no_face():
    # Regression test: a track's first sample can miss the face entirely
    # (turned away, blur, bad crop) and get recorded as no_face. Once a later
    # sample actually finds a face (status "unknown"), the display must
    # upgrade to reflect that -- observed live on-device staying stuck on
    # "No face" for 10+ seconds while the hub logged "unknown" repeatedly.
    im = IdentityMap()
    im.merge(20, _no_face())
    im.merge(20, _unknown(0.09))
    entry = im.get(20)
    assert entry["status"] == "unknown"
    assert entry["confidence"] == 0.09


def test_known_upgrades_from_no_face_too():
    im = IdentityMap()
    im.merge(20, _no_face())
    im.merge(20, _known("Jogendra", 0.9))
    entry = im.get(20)
    assert entry["status"] == "known"
    assert entry["name"] == "Jogendra"


def test_repeated_no_face_never_upgrades_to_unknown():
    im = IdentityMap()
    im.merge(20, _no_face())
    im.merge(20, _no_face())
    entry = im.get(20)
    assert entry["status"] == "no_face"


def test_same_rank_response_does_not_refresh_confidence():
    # Unchanged from before this fix: same-rank responses don't overwrite --
    # only a strictly higher-ranked status does. Documented so this doesn't
    # silently change in either direction later.
    im = IdentityMap()
    im.merge(7, _unknown(0.1))
    im.merge(7, _unknown(0.9))
    entry = im.get(7)
    assert entry["confidence"] == 0.1


def test_prune_drops_inactive_and_keeps_active():
    im = IdentityMap(inactive_grace_sec=0)
    im.merge(4, _known("Jogendra", 0.93))
    im.merge(7, _unknown(0.25))
    dropped = im.prune({4})
    assert dropped == [7]
    assert im.get(7) == {"name": "unidentified", "confidence": 0.0, "status": "unidentified"}
    assert im.get(4)["name"] == "Jogendra"


def test_snapshot_reflects_current_entries_only():
    im = IdentityMap(inactive_grace_sec=0)
    im.merge(4, _known("Jogendra", 0.93))
    im.merge(7, _unknown(0.25))
    im.prune({4})
    assert set(im.snapshot().keys()) == {4}


def test_known_identity_survives_short_same_id_gap():
    now = [10.0]
    im = IdentityMap(inactive_grace_sec=5.0, clock=lambda: now[0])
    im.prune({1})
    im.merge(1, _known("Alice", 0.61))

    now[0] = 13.0
    assert im.prune(set()) == []
    assert im.is_recent(1)
    assert im.get(1)["name"] == "Alice"

    # The same numeric ID returns and keeps its sticky known result.
    now[0] = 14.0
    im.prune({1})
    im.merge(1, _unknown(0.1))
    assert im.get(1)["name"] == "Alice"


def test_delayed_result_is_rejected_after_same_id_grace_expires():
    now = [10.0]
    im = IdentityMap(inactive_grace_sec=5.0, clock=lambda: now[0])
    im.prune({1})

    now[0] = 15.1
    assert im.prune(set()) == [1]
    assert not im.is_recent(1)


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
