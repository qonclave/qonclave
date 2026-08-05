#!/usr/bin/env python3
"""
test_track_analyze_endpoint.py — routing/response-shape smoke test for
POST /track/analyze.

Exercises framework/server.py's /track/analyze route through Flask's test
client with stub Face/Pose backends (no real models, no onnxruntime needed)
so this runs anywhere, fast. What it does NOT test: actual detection /
embedding / pose accuracy — that's face_pipeline.py's and pose_pipeline.py's
job, exercised manually via their own CLIs.

Run from the repo root:
    python hub/tests/test_track_analyze_endpoint.py
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
sys.path.insert(0, HUB_DIR)

# Keep endpoint tests from writing annotated frames into hub/track_frames/
# (read at import time by framework.server).
os.environ["QONCLAVE_TRACK_FRAMES_ENABLED"] = "0"

from framework import track_store  # noqa: E402
from framework.server import create_app  # noqa: E402


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
    _suppressed = False

    def status(self):
        return {"available": False}

    def send(self, *a, **kw):
        return False

    def recent_activity(self, limit=50):
        return []


class _StubPolicy:
    name = "test"


class _StubFaceID:
    """Returns a canned identify() result regardless of the uploaded image."""

    def __init__(self, result):
        self._result = result

    def status(self):
        return {"available": True}

    def identify(self, image_path):
        assert os.path.exists(image_path), "server must save the crop before calling identify()"
        return self._result


_KNOWN_FACE = {"available": True, "face_detected": True, "identified": True,
               "name": "Jogendra", "confidence": 0.93}

_OK_KEYPOINTS = [[10.0, float(i), 0.9] for i in range(17)]


class _StubPose:
    """Returns a canned estimate() result; records the person_box it got."""

    def __init__(self, result):
        self._result = result
        self.seen_person_boxes = []

    def status(self):
        return {"available": True, "mode": "npu"}

    def estimate(self, image_path, person_box=None):
        assert os.path.exists(image_path), "server must save the crop before calling estimate()"
        self.seen_person_boxes.append(person_box)
        return self._result


def _ok_pose():
    return _StubPose({"available": True, "status": "ok",
                      "keypoints": _OK_KEYPOINTS, "mean_score": 0.71,
                      "latency_s": 0.001, "error": None})


# A 1x1 JPEG is enough for the routing tests: the stubs never decode it.
_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300010101010101010101010101"
    "0101010101010101010101010101010101010101010101010101010101010101010101010101"
    "0101010101010101010101010101010101ffc9000b080001000101011100ffcc0006002c0300"
    "0000000000000000ffda0008010100003f00d2c000ffd9"
)

# The overlay path DOES decode its input, and _TINY_JPEG is arithmetic-coded
# (SOF9), which OpenCV refuses. This is a real 16x24 baseline JPEG for the
# annotated-frame / stream tests.
_REAL_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb0043000d090a0b0a080d0b0a0b0e0e0d"
    "0f13201513121213271c1e17202e2931302e292d2c333a4a3e333646372c2d405741464c4e52"
    "5352323e5a615a50604a51524fffdb0043010e0e0e131113261515264f352d354f4f4f4f4f4f"
    "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f"
    "4f4f4f4f4f4fffc00011080018001003012200021101031101ffc4001f000001050101010101"
    "0100000000000000000102030405060708090a0bffc400b51000020103030204030505040400"
    "00017d01020300041105122131410613516107227114328191a1082342b1c11552d1f0243362"
    "7282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a"
    "636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6"
    "a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7"
    "e8e9eaf1f2f3f4f5f6f7f8f9faffc4001f010003010101010101010101000000000000010203"
    "0405060708090a0bffc400b51100020102040403040705040400010277000102031104052131"
    "061241510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718191a"
    "262728292a35363738393a434445464748494a535455565758595a636465666768696a737475"
    "767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7"
    "b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9"
    "faffda000c03010002110311003f00a16963737bbfecd16fd98ddf3018cfd7e945dd8dcd96cf"
    "b4c5b37e76fcc0e71f4fad6cf84ffe5eff00e01ffb351e2cff00974ff81ffecb4018d697d736"
    "5bfecd2ecdf8ddf2839c7d7eb45ddf5cdeecfb4cbbf6676fca0633f4fa5145007fffd9"
)

# draw_pose_overlay() needs OpenCV. It ships with the hub's ARM64 setup (the
# same place the pose model comes from), but isn't in requirements.txt, so the
# overlay-dependent tests below no-op rather than fail on a bare install.
try:
    import cv2  # noqa: F401
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


def _make_app(face_id=None, pose=None):
    track_store.clear()  # module-level state; isolate each test's app
    return create_app(
        policy=_StubPolicy(), vlm=_StubVLM(), mqtt=_StubMQTT(), sms=_StubSMS(),
        static_dir=HERE, face_id=face_id, pose=pose,
    )


def _post_analyze(client, track_id, analyzers=None, person_box=None, jpeg=_TINY_JPEG):
    data = {
        "track_id": str(track_id),
        "image": (io.BytesIO(jpeg), "crop.jpg"),
    }
    if analyzers is not None:
        data["analyzers"] = analyzers
    if person_box is not None:
        data["person_box"] = person_box
    return client.post("/track/analyze", data=data, content_type="multipart/form-data")


# --- face analyzer status mapping (ported from test_recognize_endpoint.py) --

def test_known_face_returns_name_and_confidence():
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE))
    resp = _post_analyze(app.test_client(), 4, analyzers="face")
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["track_id"] == 4
    assert body["face"] == {"identity": "Jogendra", "confidence": 0.93, "status": "known"}
    assert "pose" not in body  # not requested
    assert "face" in body["latency_ms"]


def test_unknown_face_below_threshold():
    app = _make_app(face_id=_StubFaceID({
        "available": True, "face_detected": True, "identified": False,
        "name": None, "confidence": 0.25,
    }))
    body = _post_analyze(app.test_client(), 7, analyzers="face").get_json()
    assert body["face"] == {"identity": "unknown", "confidence": 0.25, "status": "unknown"}


def test_no_face_detected():
    app = _make_app(face_id=_StubFaceID({
        "available": True, "face_detected": False, "identified": False,
        "name": None, "confidence": None,
    }))
    body = _post_analyze(app.test_client(), 4, analyzers="face").get_json()
    assert body["face"] == {"identity": "no_face", "confidence": 0.0, "status": "no_face"}


def test_face_model_unavailable_on_hub():
    app = _make_app(face_id=_StubFaceID({"available": False}))
    resp = _post_analyze(app.test_client(), 4, analyzers="face")
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["face"] == {"identity": "unavailable", "confidence": 0.0, "status": "unavailable"}


def test_missing_track_id_is_rejected():
    app = _make_app(face_id=_StubFaceID({"available": False}))
    resp = app.test_client().post(
        "/track/analyze",
        data={"image": (io.BytesIO(_TINY_JPEG), "crop.jpg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_crop_is_deleted_after_inference():
    seen_paths = []

    class _RecordingFaceID(_StubFaceID):
        def identify(self, image_path):
            seen_paths.append(image_path)
            return super().identify(image_path)

    app = _make_app(face_id=_RecordingFaceID(_KNOWN_FACE), pose=_ok_pose())
    _post_analyze(app.test_client(), 4)
    assert len(seen_paths) == 1
    assert not os.path.exists(seen_paths[0]), "crop should be removed after inference, unlike /edge/event frames"


# --- multi-analyzer behaviour ------------------------------------------------

def test_both_analyzers_run_by_default():
    pose = _ok_pose()
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE), pose=pose)
    body = _post_analyze(app.test_client(), 4).get_json()
    assert body["face"]["status"] == "known"
    assert body["pose"] == {"status": "ok", "keypoints": _OK_KEYPOINTS, "mean_score": 0.71}
    assert set(body["latency_ms"]) == {"face", "pose"}


def test_pose_only_request_skips_face():
    face_calls = []

    class _CountingFaceID(_StubFaceID):
        def identify(self, image_path):
            face_calls.append(image_path)
            return super().identify(image_path)

    app = _make_app(face_id=_CountingFaceID(_KNOWN_FACE), pose=_ok_pose())
    body = _post_analyze(app.test_client(), 4, analyzers="pose").get_json()
    assert "face" not in body
    assert body["pose"]["status"] == "ok"
    assert face_calls == [], "face analyzer must not run when not requested"


def test_unknown_analyzer_is_rejected():
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE), pose=_ok_pose())
    resp = _post_analyze(app.test_client(), 4, analyzers="face,gait")
    assert resp.status_code == 400


def test_one_analyzer_unavailable_never_fails_the_other():
    # No pose backend at all (x86 hub / model absent): face still answers,
    # pose reports unavailable in the same 200 response.
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE), pose=None)
    resp = _post_analyze(app.test_client(), 4)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["face"]["status"] == "known"
    assert body["pose"] == {"status": "unavailable", "keypoints": None, "mean_score": None}


def test_neither_backend_enabled_still_returns_shape():
    app = _make_app(face_id=None, pose=None)
    resp = _post_analyze(app.test_client(), 4)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["face"]["status"] == "unavailable"
    assert body["pose"]["status"] == "unavailable"


def test_person_box_is_parsed_and_forwarded_to_pose():
    pose = _ok_pose()
    app = _make_app(pose=pose)
    _post_analyze(app.test_client(), 4, analyzers="pose", person_box="10,120,180,400")
    assert pose.seen_person_boxes == [(10.0, 120.0, 180.0, 400.0)]


def test_malformed_person_box_degrades_to_whole_crop():
    pose = _ok_pose()
    app = _make_app(pose=pose)
    resp = _post_analyze(app.test_client(), 4, analyzers="pose", person_box="not,numbers")
    assert resp.status_code == 200
    assert pose.seen_person_boxes == [None]


def test_results_are_recorded_in_track_store():
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE), pose=_ok_pose())
    client = app.test_client()
    _post_analyze(client, 4)
    _post_analyze(client, 4, analyzers="pose")

    tracks = client.get("/user/tracks").get_json()
    assert tracks["count"] == 1
    entry = tracks["tracks"]["4"]
    assert entry["history_len"] == 2
    # The newest sample was pose-only, but the identity resolved on the first
    # one must survive it -- that is the steady state once a track is known.
    assert entry["identity"] == "Jogendra"
    assert entry["status"] == "known"
    assert entry["latest_pose"]["status"] == "ok"
    assert entry["latest_pose"]["mean_score"] == 0.71


def test_annotated_frame_is_served_even_with_disk_retention_off():
    # QONCLAVE_TRACK_FRAMES_ENABLED=0 for this module, so nothing is written
    # to disk -- but the overlay is still published in memory for the live
    # stream, and the still endpoint serves it from there.
    if not _HAS_CV2:
        print("  (skipped: no cv2)")
        return
    app = _make_app(pose=_ok_pose())
    client = app.test_client()
    _post_analyze(client, 4, analyzers="pose", jpeg=_REAL_JPEG)
    resp = client.get("/user/tracks/4.jpg")
    assert resp.status_code == 200
    assert resp.content_type == "image/jpeg"
    assert resp.data.startswith(b"\xff\xd8")  # a real re-encoded JPEG from the overlay


def test_track_frame_404s_before_any_pose():
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE))
    client = app.test_client()
    _post_analyze(client, 4, analyzers="face")
    assert client.get("/user/tracks/4.jpg").status_code == 404


def test_no_pose_result_publishes_no_frame():
    app = _make_app(pose=_StubPose({"available": True, "status": "no_pose",
                                    "keypoints": None, "mean_score": 0.04,
                                    "latency_s": 0.001, "error": None}))
    client = app.test_client()
    _post_analyze(client, 4, analyzers="pose")
    assert client.get("/user/tracks/4.jpg").status_code == 404


def test_stream_serves_multipart_mjpeg_frames():
    if not _HAS_CV2:
        print("  (skipped: no cv2)")
        return
    app = _make_app(pose=_ok_pose())
    client = app.test_client()
    _post_analyze(client, 4, analyzers="pose", jpeg=_REAL_JPEG)  # publish one frame

    resp = client.get("/user/tracks/4/stream.mjpg")
    assert resp.status_code == 200
    assert "multipart/x-mixed-replace" in resp.content_type
    assert "boundary=frame" in resp.content_type

    # Pull just the first part off the generator, then close it -- the stream
    # is unbounded until the track goes idle.
    chunk = next(resp.response)
    resp.response.close()
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
    assert b"\xff\xd8" in chunk  # JPEG SOI marker in the payload


# --- activity feed keeps working (ported) ------------------------------------

def test_face_result_shows_up_in_activity_feed_and_serves_its_image():
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE), pose=_ok_pose())
    client = app.test_client()
    _post_analyze(client, 4)

    activity = client.get("/user/recognize_activity?limit=1").get_json()
    assert activity["count"] == 1
    entry = activity["activity"][0]
    assert entry["track_id"] == 4
    assert entry["identity"] == "Jogendra"
    assert entry["status"] == "known"
    assert "image" not in entry  # metadata only, no inline bytes

    img_resp = client.get(f"/user/recognize_activity/{entry['id']}.jpg")
    assert img_resp.status_code == 200
    assert img_resp.content_type == "image/jpeg"
    assert img_resp.data == _TINY_JPEG


def test_pose_only_request_does_not_feed_activity():
    app = _make_app(face_id=_StubFaceID(_KNOWN_FACE), pose=_ok_pose())
    client = app.test_client()
    before = client.get("/user/recognize_activity?limit=30").get_json()["count"]
    _post_analyze(client, 4, analyzers="pose")
    after = client.get("/user/recognize_activity?limit=30").get_json()["count"]
    assert after == before


def test_recognize_activity_image_404s_for_unknown_id():
    app = _make_app(face_id=_StubFaceID({"available": False}))
    resp = app.test_client().get("/user/recognize_activity/999999999.jpg")
    assert resp.status_code == 404


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
