#!/usr/bin/env python3
"""
test_recognize_endpoint.py — routing/response-shape smoke test for POST /recognize.

Exercises framework/server.py's /recognize route through Flask's test client
with a stub FaceIdentityBackend (no real models, no mediapipe/onnxruntime
needed) so this runs anywhere, fast. What it does NOT test: the actual face
detection/embedding accuracy — that's framework/face_id/face_pipeline.py's
job, exercised manually via `python hub/framework/face_id/face_pipeline.py`.

Run from the repo root:
    python hub/tests/test_recognize_endpoint.py
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
sys.path.insert(0, HUB_DIR)

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


# A 1x1 JPEG is enough: the stub never actually decodes it.
_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300010101010101010101010101"
    "0101010101010101010101010101010101010101010101010101010101010101010101010101"
    "0101010101010101010101010101010101ffc9000b080001000101011100ffcc0006002c0300"
    "0000000000000000ffda0008010100003f00d2c000ffd9"
)


def _make_app(face_id):
    return create_app(
        policy=_StubPolicy(), vlm=_StubVLM(), mqtt=_StubMQTT(), sms=_StubSMS(),
        static_dir=HERE, face_id=face_id,
    )


def _post_recognize(client, track_id, result):
    return client.post(
        "/recognize",
        data={
            "track_id": str(track_id),
            "image": (io.BytesIO(_TINY_JPEG), "crop.jpg"),
        },
        content_type="multipart/form-data",
    )


def test_known_face_returns_name_and_confidence():
    app = _make_app(_StubFaceID({
        "available": True, "face_detected": True, "identified": True,
        "name": "Jogendra", "confidence": 0.93,
    }))
    resp = _post_recognize(app.test_client(), 4, None)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body == {"track_id": 4, "identity": "Jogendra", "confidence": 0.93, "status": "known"}


def test_unknown_face_below_threshold():
    app = _make_app(_StubFaceID({
        "available": True, "face_detected": True, "identified": False,
        "name": None, "confidence": 0.25,
    }))
    resp = _post_recognize(app.test_client(), 7, None)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body == {"track_id": 7, "identity": "unknown", "confidence": 0.25, "status": "unknown"}


def test_no_face_detected():
    app = _make_app(_StubFaceID({
        "available": True, "face_detected": False, "identified": False,
        "name": None, "confidence": None,
    }))
    resp = _post_recognize(app.test_client(), 4, None)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body == {"track_id": 4, "identity": "no_face", "confidence": 0.0, "status": "no_face"}


def test_model_unavailable_on_hub():
    app = _make_app(_StubFaceID({"available": False}))
    resp = _post_recognize(app.test_client(), 4, None)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body == {"track_id": 4, "identity": "unavailable", "confidence": 0.0, "status": "unavailable"}


def test_face_id_not_enabled_on_hub_at_all():
    app = _make_app(face_id=None)
    resp = _post_recognize(app.test_client(), 4, None)
    body = resp.get_json()
    assert resp.status_code == 503, body
    assert body["status"] == "unavailable"


def test_missing_track_id_is_rejected():
    app = _make_app(_StubFaceID({"available": False}))
    resp = app.test_client().post(
        "/recognize",
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

    app = _make_app(_RecordingFaceID({
        "available": True, "face_detected": True, "identified": True,
        "name": "Jogendra", "confidence": 0.9,
    }))
    _post_recognize(app.test_client(), 4, None)
    assert len(seen_paths) == 1
    assert not os.path.exists(seen_paths[0]), "crop should be removed after inference, unlike /edge/event frames"


def test_recognize_call_shows_up_in_activity_feed_and_serves_its_image():
    app = _make_app(_StubFaceID({
        "available": True, "face_detected": True, "identified": True,
        "name": "Jogendra", "confidence": 0.93,
    }))
    client = app.test_client()
    _post_recognize(client, 4, None)

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


def test_recognize_activity_image_404s_for_unknown_id():
    app = _make_app(_StubFaceID({"available": False}))
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
