"""
pose.py — conditional pose estimation for the Qonclave framework.

Wraps pose_pipeline.py (HRNetPose w8a8 on the Hexagon NPU) behind the same
lazy-load, always-returns-a-dict contract as face_id/identity.py's
FaceIdentityBackend: the heavy imports (onnxruntime, onnxruntime-qnn, cv2)
only happen inside pose_pipeline's functions, never at module import time, so
this module is safe to import on any hub machine. estimate() reports
{"available": False, ...} wherever the model or its dependencies aren't
present, instead of raising.

Unlike face ID (which has a PyTorch CPU path on any platform), pose only
loads on ARM64 with an exported ONNX model present — everywhere else it
reports unavailable and the rest of the hub keeps working.

Public API:
    pose = PoseBackend()
    pose.is_available()
    pose.warmup()
    pose.status()
    result = pose.estimate(image_path, person_box=None)  # -> dict
"""

from __future__ import annotations

import logging
import platform
import threading
import time

from . import pose_pipeline as pp

log = logging.getLogger("qonclave.pose")

# Below this mean keypoint confidence the heatmaps are noise — the crop
# doesn't contain a usable person (occluded, truncated, or not a person).
NO_POSE_MEAN_SCORE = 0.15


def _is_arm64() -> bool:
    m = platform.machine().upper()
    return "ARM64" in m or "AARCH64" in m


class PoseBackend:
    """Lazily loads the HRNetPose session. Safe to construct on any machine."""

    def __init__(self):
        self._session = None
        self._input_name: str | None = None
        self._load_error: str | None = None
        self._load_attempted = False
        self._lock = threading.Lock()  # inference serialized, mirrors FaceIdentityBackend

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        if self._session is not None:
            return True
        if self._load_attempted:
            return False
        return self._try_load()

    def status(self) -> dict:
        # mode comes from the session's RESOLVED provider (qnn_session falls
        # back to CPU silently on any QNN failure), not from what was asked
        # for — so a "working, just slow" CPU fallback is visible here.
        from ..qnn_session import session_mode

        model = pp.model_path()
        return {
            "available": self._session is not None,
            "mode": session_mode(self._session) if self._session else None,
            "model": model.name if model else None,
            "load_attempted": self._load_attempted,
            "load_error": self._load_error,
        }

    def warmup(self) -> bool:
        """Eagerly load the model; returns True on success."""
        return self._try_load()

    # --- internal -------------------------------------------------------------
    def _try_load(self) -> bool:
        with self._lock:
            if self._session is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True

            if not _is_arm64():
                self._load_error = "pose runs on ARM64 (Snapdragon) hubs only"
                log.info("Pose unavailable: %s", self._load_error)
                return False
            if pp.model_path() is None:
                self._load_error = (
                    f"pose model not found: {pp.RAW_ONNX_PATH} "
                    "(run hub/framework/pose/setup/setup_pose.ps1)"
                )
                log.info("Pose unavailable: %s", self._load_error)
                return False

            try:
                t0 = time.time()
                self._session, self._input_name = pp.build_session()
                log.info("Pose model loaded in %.1fs (%s)",
                         time.time() - t0, self.status()["mode"])
                return True
            except Exception as e:
                self._load_error = f"model load failed: {e}"
                log.warning("Pose unavailable: %s", self._load_error)
                self._session = None
                self._input_name = None
                return False

    # --- inference ------------------------------------------------------------
    def estimate(self, image_path: str, person_box=None) -> dict:
        """
        Estimate the pose of the (single) person in image_path.

        person_box: optional tight (x1, y1, x2, y2) person rect in image
        pixels — the edge sends its unpadded tracker box so the model sees the
        subject at full input height instead of face-framed headroom. None
        falls back to the whole image.

        Always returns a dict; never raises for the caller.

        {"available": bool, "status": "ok"|"no_pose"|"unavailable",
         "keypoints": [[x, y, score], ...17] | None,   # image pixels
         "mean_score": float|None, "latency_s": float|None, "error": str|None}
        """
        if not self.is_available():
            return {
                "available": False, "status": "unavailable", "keypoints": None,
                "mean_score": None, "latency_s": None,
                "error": self._load_error or "pose not available on this hub",
            }

        with self._lock:
            t0 = time.time()
            try:
                import cv2

                img = cv2.imread(image_path, cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError(f"cannot decode image: {image_path}")
                box = self._sanitize_box(person_box, img.shape[1], img.shape[0])
                kps = pp.estimate(self._session, self._input_name, img, box)
            except Exception as e:
                log.exception("Pose estimate() failed")
                return {
                    "available": True, "status": "unavailable", "keypoints": None,
                    "mean_score": None, "latency_s": None,
                    "error": f"estimate failed: {e}",
                }
            latency = round(time.time() - t0, 3)

        mean_score = float(kps[:, 2].mean())
        if mean_score < NO_POSE_MEAN_SCORE:
            return {
                "available": True, "status": "no_pose", "keypoints": None,
                "mean_score": round(mean_score, 4), "latency_s": latency,
                "error": None,
            }

        return {
            "available": True, "status": "ok",
            "keypoints": [[round(float(x), 1), round(float(y), 1), round(float(s), 4)]
                          for x, y, s in kps],
            "mean_score": round(mean_score, 4), "latency_s": latency,
            "error": None,
        }

    @staticmethod
    def _sanitize_box(person_box, w: int, h: int):
        """Clamp a caller-supplied box to the image; None/degenerate -> None
        (whole image). Never raises — a malformed box from the wire must not
        take down the request."""
        if person_box is None:
            return None
        try:
            x1, y1, x2, y2 = (float(v) for v in person_box)
        except (TypeError, ValueError):
            return None
        x1, x2 = max(0.0, min(x1, w)), max(0.0, min(x2, w))
        y1, y2 = max(0.0, min(y1, h)), max(0.0, min(y2, h))
        if x2 - x1 < 1 or y2 - y1 < 1:
            return None
        return (x1, y1, x2, y2)

    def close(self):
        with self._lock:
            self._session = None
            self._input_name = None
