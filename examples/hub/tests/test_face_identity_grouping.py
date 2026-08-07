"""Multiple photos per person resolve to ONE enrolled identity.

Why this matters beyond recognition accuracy: posture timers (posture.py keys
its abnormal/motionless state by identity) and follow priorities are keyed by
the identity string. Two photos of one person read as two people make the
match flip between them, and each flip resets a collapsing person's timers.
"""

import os
import sys

import numpy as np
import pytest

HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HUB_DIR)

from framework.face_id import face_pipeline as fp
from framework.face_id.identity import FaceIdentityBackend


def unit(*values) -> np.ndarray:
    v = np.asarray(values, dtype=float)
    return v / np.linalg.norm(v)


# --- identity_for_path: the one definition of the layout --------------------

def test_flat_filename_is_the_identity(tmp_path):
    assert fp.identity_for_path(tmp_path / "priya.jpg", tmp_path) == "priya"


def test_double_underscore_groups_photos_of_one_person(tmp_path):
    for name in ("priya__2.jpg", "priya__3.png", "priya__side.jpg"):
        assert fp.identity_for_path(tmp_path / name, tmp_path) == "priya"


def test_single_underscore_stays_part_of_the_name(tmp_path):
    # _slugify_name collapses runs of non-alphanumerics to ONE underscore, so a
    # real slug can never contain "__" -- which is what makes it a safe
    # separator, and why a single underscore must keep its old meaning.
    assert fp.identity_for_path(tmp_path / "priya_1.jpg", tmp_path) == "priya_1"
    assert fp.identity_for_path(tmp_path / "bob_smith.jpg", tmp_path) == "bob_smith"


def test_case_does_not_split_one_person_in_two(tmp_path):
    # Grouping exists so hand-named files land on one person; a capital letter
    # must not undo that. Matches _slugify_name, which lowercases, so a
    # hand-dropped file and a dashboard enrollment agree on the name.
    for name in ("Priya__1.jpg", "priya__2.jpg", "PRIYA__3.png",
                 "Priya.jpg"):
        assert fp.identity_for_path(tmp_path / name, tmp_path) == "priya"
    assert fp.identity_for_path(tmp_path / "PRIYA" / "side.jpg",
                                tmp_path) == "priya"


def test_subdirectory_name_is_the_identity(tmp_path):
    p = tmp_path / "priya" / "side.jpg"
    assert fp.identity_for_path(p, tmp_path) == "priya"
    deeper = tmp_path / "priya" / "angles" / "left.jpg"
    assert fp.identity_for_path(deeper, tmp_path) == "priya"


# --- match_scores: best across a person's photos, not the mean --------------

def test_best_photo_wins_not_the_average():
    # A frontal and a profile embedding average into something matching
    # neither, so a mean template would make extra photos HURT. The max means
    # each photo can independently rescue a match.
    frontal, profile = unit(1, 0), unit(0, 1)
    known = {"priya": np.stack([frontal, profile])}

    assert fp.match_scores(known, frontal)["priya"] == pytest.approx(1.0)
    assert fp.match_scores(known, profile)["priya"] == pytest.approx(1.0)

    # The mean of those two would only score ~0.71 against either pose.
    mean_template = {"priya": (frontal + profile) / np.linalg.norm(frontal + profile)}
    assert fp.match_scores(mean_template, frontal)["priya"] < 0.75


def test_adding_a_photo_never_lowers_a_persons_score():
    target = unit(1, 0, 0)
    one = {"p": np.stack([unit(0.9, 0.1, 0)])}
    two = {"p": np.stack([unit(0.9, 0.1, 0), unit(0, 0, 1)])}
    assert fp.match_scores(two, target)["p"] >= fp.match_scores(one, target)["p"]


def test_legacy_1d_cache_entries_still_score():
    # A .embeddings_*.npy written before grouping holds one 1-D array per
    # person; it must stay readable rather than crashing on the first inference.
    known = {"alice": unit(1, 0)}
    assert fp.match_scores(known, unit(1, 0))["alice"] == pytest.approx(1.0)


# --- _load_db grouping ------------------------------------------------------

def fake_embeddings(monkeypatch, mapping):
    """Stub get_embedding so _load_db needs no model. Keyed by file name."""
    def _embed(detector, model, image_path, use_npu):
        return mapping.get(os.path.basename(image_path))
    monkeypatch.setattr(fp, "get_embedding", _embed)


def test_load_db_groups_multiple_photos_into_one_person(tmp_path, monkeypatch):
    for name in ("priya.jpg", "priya__2.jpg", "alice.jpg"):
        (tmp_path / name).write_bytes(b"x")
    fake_embeddings(monkeypatch, {
        "priya.jpg": unit(1, 0, 0),
        "priya__2.jpg": unit(0, 1, 0),
        "alice.jpg": unit(0, 0, 1),
    })

    known = fp._load_db(None, None, tmp_path, use_npu=False)
    assert sorted(known) == ["alice", "priya"]
    assert known["priya"].shape == (2, 3)  # both photos kept
    assert known["alice"].shape == (1, 3)

    # Either priya photo identifies him; neither is diluted by the other.
    assert fp.match_scores(known, unit(0, 1, 0))["priya"] == pytest.approx(1.0)


def test_load_db_groups_per_person_subdirectories(tmp_path, monkeypatch):
    (tmp_path / "priya").mkdir()
    (tmp_path / "priya" / "front.jpg").write_bytes(b"x")
    (tmp_path / "priya" / "side.jpg").write_bytes(b"x")
    fake_embeddings(monkeypatch, {"front.jpg": unit(1, 0), "side.jpg": unit(0, 1)})

    known = fp._load_db(None, None, tmp_path, use_npu=False)
    assert sorted(known) == ["priya"]
    assert known["priya"].shape == (2, 2)


def test_photo_with_no_detectable_face_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "priya.jpg").write_bytes(b"x")
    (tmp_path / "priya__2.jpg").write_bytes(b"x")
    fake_embeddings(monkeypatch, {"priya.jpg": unit(1, 0)})  # __2 returns None

    known = fp._load_db(None, None, tmp_path, use_npu=False)
    assert known["priya"].shape == (1, 2)


def test_stale_cache_listing_old_identities_is_rebuilt(tmp_path, monkeypatch):
    # The regression that motivated this: renaming priya_1.jpg to
    # priya__2.jpg can preserve mtimes, so an mtime-only check would keep
    # serving "priya_1" as a separate person forever.
    (tmp_path / "priya.jpg").write_bytes(b"x")
    (tmp_path / "priya__2.jpg").write_bytes(b"x")
    stale = {"priya": unit(1, 0), "priya_1": unit(0, 1)}
    np.save(str(fp._cache_path(tmp_path, False)), stale)
    # Make the cache look newer than every image.
    os.utime(fp._cache_path(tmp_path, False), (1 << 31, 1 << 31))

    fake_embeddings(monkeypatch, {
        "priya.jpg": unit(1, 0), "priya__2.jpg": unit(0, 1),
    })
    known = fp._load_db(None, None, tmp_path, use_npu=False)
    assert sorted(known) == ["priya"]  # not priya_1


def test_fresh_cache_matching_the_directory_is_reused(tmp_path, monkeypatch):
    (tmp_path / "alice.jpg").write_bytes(b"x")
    np.save(str(fp._cache_path(tmp_path, False)), {"alice": unit(1, 0)})
    os.utime(fp._cache_path(tmp_path, False), (1 << 31, 1 << 31))

    def _explode(*a, **kw):
        raise AssertionError("should have used the cache")
    monkeypatch.setattr(fp, "get_embedding", _explode)

    known = fp._load_db(None, None, tmp_path, use_npu=False)
    assert sorted(known) == ["alice"]
    assert known["alice"].shape == (1, 2)  # normalized to 2-D for callers


# --- backend roster + enrollment -------------------------------------------

def offline_backend(faces_dir, monkeypatch) -> FaceIdentityBackend:
    """A backend with the models reported unavailable.

    These tests exercise file management, not inference. Left unstubbed on a
    machine that HAS the models (this repo's ARM64 hub does), enroll() would
    load CavaFace on the NPU -- ~40s -- and then reject the dummy image bytes
    for containing no face. enroll() deliberately still saves the file when the
    model is absent, so hubs without face-ID can enroll; that is the path here.
    """
    monkeypatch.setattr(FaceIdentityBackend, "is_available", lambda self: False)
    return FaceIdentityBackend(known_faces_dir=faces_dir)


def test_known_names_reports_one_name_per_person(tmp_path):
    for name in ("priya.jpg", "priya__2.jpg", "priya__3.png"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "front.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")  # non-image ignored

    backend = FaceIdentityBackend(known_faces_dir=tmp_path)
    assert backend.known_names() == ["alice", "priya"]


def test_photos_for_lists_every_angle(tmp_path):
    for name in ("priya.jpg", "priya__2.jpg", "alice.jpg"):
        (tmp_path / name).write_bytes(b"x")
    backend = FaceIdentityBackend(known_faces_dir=tmp_path)
    assert [p.name for p in backend.photos_for("priya")] == [
        "priya.jpg", "priya__2.jpg"]
    assert [p.name for p in backend.photos_for("Priya")] == [
        "priya.jpg", "priya__2.jpg"]  # name is slugified


def test_enroll_additional_keeps_existing_photos(tmp_path, monkeypatch):
    faces, staging = tmp_path / "faces", tmp_path / "upload.jpg"
    faces.mkdir()
    (faces / "priya.jpg").write_bytes(b"first")
    staging.write_bytes(b"second")

    backend = offline_backend(faces, monkeypatch)
    result = backend.enroll("Priya", str(staging), additional=True)

    assert result["ok"] is True
    assert result["replaced"] is False
    assert result["photo_count"] == 2
    assert os.path.basename(result["path"]) == "priya__2.jpg"
    assert (faces / "priya.jpg").read_bytes() == b"first"  # untouched
    assert backend.known_names() == ["priya"]  # still ONE person


def test_enroll_additional_picks_the_next_free_index(tmp_path, monkeypatch):
    faces, staging = tmp_path / "faces", tmp_path / "upload.jpg"
    faces.mkdir()
    (faces / "priya.jpg").write_bytes(b"a")
    (faces / "priya__2.png").write_bytes(b"b")
    staging.write_bytes(b"c")

    backend = offline_backend(faces, monkeypatch)
    result = backend.enroll("priya", str(staging), additional=True)
    # __2 is taken under a different extension; it must not be overwritten.
    assert os.path.basename(result["path"]) == "priya__3.jpg"
    assert (faces / "priya__2.png").read_bytes() == b"b"
    assert result["photo_count"] == 3


def test_enroll_default_replaces_every_photo_of_that_person(tmp_path, monkeypatch):
    faces, staging = tmp_path / "faces", tmp_path / "upload.jpg"
    faces.mkdir()
    (faces / "priya.jpg").write_bytes(b"a")
    (faces / "priya__2.png").write_bytes(b"b")
    (faces / "alice.jpg").write_bytes(b"keep")
    staging.write_bytes(b"new")

    backend = offline_backend(faces, monkeypatch)
    result = backend.enroll("priya", str(staging))

    assert result["replaced"] is True
    assert result["photo_count"] == 1
    assert not (faces / "priya__2.png").exists()  # no stale angle survives
    assert (faces / "priya.jpg").read_bytes() == b"new"
    assert (faces / "alice.jpg").read_bytes() == b"keep"  # other people safe


def test_enroll_first_photo_of_a_new_person_has_no_suffix(tmp_path, monkeypatch):
    faces, staging = tmp_path / "faces", tmp_path / "upload.jpg"
    faces.mkdir()
    staging.write_bytes(b"a")
    backend = offline_backend(faces, monkeypatch)
    result = backend.enroll("Alice", str(staging), additional=True)
    assert os.path.basename(result["path"]) == "alice.jpg"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

