#!/usr/bin/env python3
"""
test_pose_backend.py — PoseBackend's degradation contract: on a host with no
model present (or a non-ARM64 host) it must report unavailable from status()
and estimate(), and never raise. The happy path (real NPU inference) is
exercised manually via `python hub/framework/pose/pose_pipeline.py benchmark`.

Run from the repo root:
    python hub/tests/test_pose_backend.py
"""

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
sys.path.insert(0, HUB_DIR)

from framework.pose import pose_pipeline as pp  # noqa: E402
from framework.pose.pose import PoseBackend  # noqa: E402


class _no_model:
    """Temporarily point the pipeline's model paths at an empty directory, so
    the contract can be tested even on the Snapdragon hub where the real
    export exists."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (pp.RAW_ONNX_PATH, pp.CTX_ONNX_PATH)
        pp.RAW_ONNX_PATH = Path(self._tmp.name) / "hrnet_pose.onnx"
        pp.CTX_ONNX_PATH = Path(self._tmp.name) / "hrnet_pose_ctx.onnx"
        return self

    def __exit__(self, *exc):
        pp.RAW_ONNX_PATH, pp.CTX_ONNX_PATH = self._saved
        self._tmp.cleanup()
        return False


def test_status_before_any_load_attempt():
    with _no_model():
        b = PoseBackend()
        s = b.status()
        assert s["available"] is False
        assert s["mode"] is None
        assert s["load_attempted"] is False
        assert s["load_error"] is None


def test_missing_model_reports_unavailable_and_never_raises():
    with _no_model():
        b = PoseBackend()
        assert b.is_available() is False
        assert b.warmup() is False
        s = b.status()
        assert s["available"] is False
        assert s["load_attempted"] is True
        assert s["load_error"]  # says why


def test_estimate_returns_unavailable_dict_not_exception():
    with _no_model():
        b = PoseBackend()
        result = b.estimate("nonexistent.jpg", person_box=(0, 0, 10, 10))
        assert result == {
            "available": False, "status": "unavailable", "keypoints": None,
            "mean_score": None, "latency_s": None, "error": result["error"],
        }
        assert result["error"]


def test_load_is_attempted_only_once():
    with _no_model():
        b = PoseBackend()
        assert b.is_available() is False
        first_error = b.status()["load_error"]
        assert b.is_available() is False  # cached verdict, no re-raise/re-log
        assert b.status()["load_error"] == first_error


def test_sanitize_box_clamps_and_rejects_degenerate():
    sanitize = PoseBackend._sanitize_box
    assert sanitize(None, 100, 100) is None
    assert sanitize((0, 0, 50, 80), 100, 100) == (0.0, 0.0, 50.0, 80.0)
    assert sanitize((-20, -5, 150, 120), 100, 100) == (0.0, 0.0, 100.0, 100.0)
    assert sanitize((10, 10, 10, 40), 100, 100) is None      # zero width
    assert sanitize((200, 200, 300, 300), 100, 100) is None  # fully outside
    assert sanitize(("a", 0, 10, 10), 100, 100) is None      # malformed
    assert sanitize((1, 2, 3), 100, 100) is None             # wrong arity


def test_preprocess_matches_declared_model_input_type():
    image = np.full((32, 24, 3), 128, dtype=np.uint8)
    crop = (0, 0, 24, 32)
    float_input = pp.preprocess(image, crop, "tensor(float)")
    uint8_input = pp.preprocess(image, crop, "tensor(uint8)")
    assert float_input.shape == uint8_input.shape == (1, 3, pp.IN_H, pp.IN_W)
    assert float_input.dtype == np.float32
    assert uint8_input.dtype == np.uint8
    assert 0.0 <= float_input.min() <= float_input.max() <= 1.0


def test_decode_heatmaps_accepts_float_and_quantized_boundaries():
    hm_u8 = np.full((1, 17, pp.HM_H, pp.HM_W), pp.OUT_ZP, dtype=np.uint8)
    hm_u8[:, :, 10, 12] = pp.OUT_ZP + 20
    hm_float = (hm_u8.astype(np.float32) - pp.OUT_ZP) * pp.OUT_SCALE
    crop = (0, 0, pp.IN_W, pp.IN_H)
    np.testing.assert_allclose(
        pp.decode_heatmaps(hm_u8, crop),
        pp.decode_heatmaps(hm_float, crop),
    )


def run_all():
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    run_all()
