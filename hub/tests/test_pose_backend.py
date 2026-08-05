"""
test_pose_backend.py — PoseBackend's contract on a host with no model.

That is the state of every machine until an AI Hub export has run, including CI
and every x86 laptop, so it is the state most worth pinning down. The rule is
the same one FaceIdentityBackend follows: report unavailable, never raise, and
never stop the rest of the hub from working.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.pose.pose import PoseBackend  # noqa: E402


def test_constructing_is_cheap_and_import_free():
    """__init__ must not touch onnxruntime. A hub that cannot import the pose
    stack still has to start."""
    backend = PoseBackend()
    status = backend.status()
    assert status["available"] is False
    assert status["load_attempted"] is False


def test_status_reports_unavailable_without_a_model():
    backend = PoseBackend()
    backend.is_available()
    status = backend.status()
    assert status["available"] is False
    assert status["load_attempted"] is True
    assert status["load_error"]


def test_estimate_never_raises_without_a_model():
    result = PoseBackend().estimate("nonexistent.jpg")
    assert result["available"] is False
    assert result["status"] == "unavailable"
    assert result["keypoints"] is None
    assert result["error"]


def test_estimate_never_raises_on_a_missing_file():
    result = PoseBackend().estimate("/definitely/not/here.jpg", person_box=(0, 0, 10, 10))
    assert result["status"] == "unavailable"


def test_load_is_attempted_once():
    """A hub polling /health every 15s must not retry a 6-second model load
    each time."""
    backend = PoseBackend()
    assert backend.is_available() is False
    error_first = backend.status()["load_error"]
    assert backend.is_available() is False
    assert backend.status()["load_error"] == error_first


def test_warmup_is_safe_without_a_model():
    assert PoseBackend().warmup() is False


def test_result_shape_is_stable_across_failures():
    """Callers read these keys unconditionally, so they must always be present
    — an unavailable pose still has to answer the same questions."""
    for result in (PoseBackend().estimate("a.jpg"),
                   PoseBackend().estimate("b.jpg", person_box=(1, 1, 5, 5))):
        assert set(result) == {"available", "status", "keypoints",
                               "mean_score", "latency_s", "error"}
