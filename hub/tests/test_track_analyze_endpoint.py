"""
test_track_analyze_endpoint.py — the unified per-track analysis endpoint.

Replaces test_recognize_endpoint.py. Keeps its two load-bearing assertions — the
stub-backend approach and "the uploaded crop is deleted" — and adds what the
unified endpoint introduces: per-analyzer selection, and partial availability.

Partial availability is the one worth stating plainly. Face and pose fail
independently, and a hub with no pose model must still identify faces. If one
analyzer being down could fail the other, every deployment without an AI Hub
export would lose face ID too.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework import track_store  # noqa: E402
from framework.policy import Policy, Verdict  # noqa: E402
from framework.server import create_app  # noqa: E402


class _StubPolicy(Policy):
    name = "stub"

    def evaluate(self, event, image_path=None):
        return Verdict(verified=False, confidence=None, alert="stub")


class _Backend:
    def status(self):
        return {"available": False}


class _StubFaceId(_Backend):
    """Records the paths it was handed, so the delete-after assertion can check
    the file existed at inference time and not merely that it is gone now."""

    def __init__(self, result=None):
        self.seen_paths = []
        self.result = result if result is not None else {
            "available": True, "face_detected": True, "identified": True,
            "name": "Jogendra", "confidence": 0.93,
        }

    def identify(self, path):
        self.seen_paths.append(path)
        assert os.path.exists(path), "face ID was handed a path that does not exist"
        return dict(self.result)


class _StubPose(_Backend):
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {
            "available": True, "status": "ok",
            "keypoints": [[10.0, 20.0, 0.9]] * 17,
            "mean_score": 0.71, "latency_s": 0.0015, "error": None,
        }

    def estimate(self, path, person_box=None):
        self.calls.append((path, person_box))
        return dict(self.result)


@pytest.fixture(autouse=True)
def _clean_store():
    track_store.clear()
    yield
    track_store.clear()


def build(face_id=None, pose=None, tmp_path=None, frames=False):
    import framework.server as srv

    srv.TRACK_FRAMES_ENABLED = frames
    if tmp_path is not None:
        srv.TRACK_FRAMES_DIR = str(tmp_path / "track_frames")
        static = tmp_path / "static"
    else:
        static = None

    if static is not None:
        static.mkdir(exist_ok=True)
        (static / "dashboard.html").write_text("<html></html>", encoding="utf-8")

    app = create_app(policy=_StubPolicy(), vlm=_Backend(), mqtt=_Backend(),
                     sms=_Backend(), static_dir=str(static) if static else ".",
                     face_id=face_id, pose=pose)
    app.config["TESTING"] = True
    return app.test_client()


def post(client, **kw):
    data = {"track_id": kw.pop("track_id", 4), "image": (kw.pop("image", None) or _jpeg())}
    data.update(kw)
    return client.post("/track/analyze", data=data, content_type="multipart/form-data")


def _jpeg():
    import io
    return (io.BytesIO(b"\xff\xd8\xff\xe0 fake jpeg"), "crop.jpg")


# --- both analyzers ---------------------------------------------------------

def test_both_analyzers_run_by_default(tmp_path):
    face, pose = _StubFaceId(), _StubPose()
    body = post(build(face, pose, tmp_path)).get_json()
    assert body["track_id"] == 4
    assert body["face"]["status"] == "known"
    assert body["face"]["identity"] == "Jogendra"
    assert body["pose"]["status"] == "ok"
    assert len(body["pose"]["keypoints"]) == 17
    assert set(body["latency_ms"]) == {"face", "pose"}


@pytest.mark.parametrize("analyzers,expect", [
    ("face", {"face"}),
    ("pose", {"pose"}),
    ("face,pose", {"face", "pose"}),
    (" FACE , Pose ", {"face", "pose"}),
])
def test_only_requested_analyzers_run(tmp_path, analyzers, expect):
    face, pose = _StubFaceId(), _StubPose()
    body = post(build(face, pose, tmp_path), analyzers=analyzers).get_json()
    assert set(body["latency_ms"]) == expect
    assert bool(face.seen_paths) is ("face" in expect)
    assert bool(pose.calls) is ("pose" in expect)


# --- partial availability ---------------------------------------------------

def test_missing_pose_backend_does_not_break_face(tmp_path):
    """Every hub without an AI Hub export is in this state."""
    face = _StubFaceId()
    body = post(build(face, None, tmp_path)).get_json()
    assert body["face"]["status"] == "known"
    assert body["pose"]["status"] == "unavailable"
    assert body["pose"]["error"]


def test_missing_face_backend_does_not_break_pose(tmp_path):
    pose = _StubPose()
    body = post(build(None, pose, tmp_path)).get_json()
    assert body["face"]["status"] == "unavailable"
    assert body["pose"]["status"] == "ok"


def test_pose_failure_is_reported_not_raised(tmp_path):
    pose = _StubPose({"available": False, "status": "unavailable", "keypoints": None,
                      "mean_score": None, "latency_s": None, "error": "no model"})
    body = post(build(_StubFaceId(), pose, tmp_path)).get_json()
    assert body["pose"]["status"] == "unavailable"
    assert body["face"]["status"] == "known"


# --- the crop must not persist ----------------------------------------------

def test_uploaded_crop_is_deleted(tmp_path):
    """A sampled body crop is transient by design — unlike an escalation frame,
    the privacy cascade says it must not persist."""
    face = _StubFaceId()
    post(build(face, _StubPose(), tmp_path))
    assert face.seen_paths, "face ID never ran, so this proves nothing"
    assert not os.path.exists(face.seen_paths[0])


def test_crop_is_deleted_even_when_an_analyzer_raises(tmp_path):
    class _Exploding(_Backend):
        def estimate(self, path, person_box=None):
            raise RuntimeError("boom")

    face = _StubFaceId()
    client = build(face, _Exploding(), tmp_path)
    with pytest.raises(RuntimeError):
        post(client)
    assert not os.path.exists(face.seen_paths[0]), "the finally: clause did not run"


# --- person_box -------------------------------------------------------------

def test_person_box_reaches_the_pose_backend(tmp_path):
    pose = _StubPose()
    post(build(_StubFaceId(), pose, tmp_path), person_box="10,20,110,220")
    assert pose.calls[0][1] == (10, 20, 110, 220)


@pytest.mark.parametrize("bad", ["", "nope", "1,2,3", "10,20,5,5"])
def test_bad_person_box_falls_back_to_the_whole_crop(tmp_path, bad):
    """Worse framing, not an error. Rejecting the request would lose an
    observation over a field that is optional by design."""
    pose = _StubPose()
    body = post(build(_StubFaceId(), pose, tmp_path), person_box=bad).get_json()
    assert pose.calls[0][1] is None
    assert body["pose"]["status"] == "ok"


# --- request validation -----------------------------------------------------

def test_missing_track_id_is_a_400(tmp_path):
    client = build(_StubFaceId(), _StubPose(), tmp_path)
    resp = client.post("/track/analyze", data={"image": _jpeg()},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_non_integer_track_id_is_a_400(tmp_path):
    resp = post(build(_StubFaceId(), _StubPose(), tmp_path), track_id="abc")
    assert resp.status_code == 400


def test_missing_image_is_a_400(tmp_path):
    client = build(_StubFaceId(), _StubPose(), tmp_path)
    resp = client.post("/track/analyze", data={"track_id": 4},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


# --- the store and the dashboard routes -------------------------------------

def test_result_is_recorded_in_the_track_store(tmp_path):
    post(build(_StubFaceId(), _StubPose(), tmp_path), track_id=7)
    snap = track_store.snapshot()
    assert 7 in snap
    assert snap[7]["identity"] == "Jogendra"
    assert snap[7]["history_len"] == 1


def test_user_tracks_route(tmp_path):
    client = build(_StubFaceId(), _StubPose(), tmp_path)
    post(client, track_id=3)
    body = client.get("/user/tracks").get_json()
    assert "3" in body["tracks"] or 3 in body["tracks"]


def test_track_frame_route_404s_without_a_frame(tmp_path):
    client = build(_StubFaceId(), _StubPose(), tmp_path, frames=False)
    post(client, track_id=9)
    assert client.get("/user/tracks/9.jpg").status_code == 404


def test_annotated_frame_is_written_when_enabled(tmp_path):
    client = build(_StubFaceId(), _StubPose(), tmp_path, frames=True)
    post(client, track_id=5)
    assert client.get("/user/tracks/5.jpg").status_code == 200


def test_frames_are_not_written_when_disabled(tmp_path):
    """QONCLAVE_TRACK_FRAMES_ENABLED=0 is the setting for any non-demo
    deployment, so it has to actually stop imagery landing on disk."""
    client = build(_StubFaceId(), _StubPose(), tmp_path, frames=False)
    post(client, track_id=5)
    frames_dir = tmp_path / "track_frames"
    assert not frames_dir.exists() or not list(frames_dir.glob("*.jpg"))
