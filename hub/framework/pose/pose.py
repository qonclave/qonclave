"""
pose.py — PoseBackend: hub-side pose estimation, tied to an edge track_id.

Contract-identical to `face_id/identity.FaceIdentityBackend`: cheap import-free
__init__, lazy load, `is_available()`, `warmup()`, `status()`, a lock around
inference, and it **never raises**. A hub with no pose model, or on a non-ARM64
host, keeps serving everything else and reports pose as unavailable.

`status()` reports the resolved mode (`npu` / `cpu`) rather than assuming. The
QNN session builder degrades to CPU silently by design, so a missing model or an
unregistered EP otherwise looks like "working, just slow" — roughly 45 ms
instead of 1.4 ms. That is the failure this class is most likely to hide, so it
is the one it reports loudest.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("qonclave.pose")

# Below this mean keypoint score the estimate is noise rather than a person.
# A crop that is too small or too occluded lands here rather than producing
# confident nonsense, which matters because fall logic will read these.
MIN_MEAN_SCORE = 0.15


class PoseBackend:
    """HRNetPose on the Hexagon NPU, or a clean 'unavailable' if it cannot load."""

    def __init__(self, min_mean_score: float = MIN_MEAN_SCORE):
        self.min_mean_score = min_mean_score
        self._session = None
        self._input_name = None
        self._mode = None
        self._meta = None
        self._load_attempted = False
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        if self._session is not None:
            return True
        if self._load_attempted:
            return False
        return self._load()

    def status(self) -> dict:
        return {
            "available": self._session is not None,
            "mode": self._mode,
            "model": "hrnet_pose_w8a8",
            "load_attempted": self._load_attempted,
            "load_error": self._load_error,
        }

    def warmup(self) -> bool:
        """Load the model now instead of on the first request.

        Session init is ~0.3 s with the context binary and ~6 s without, and the
        first request should not pay either.
        """
        return self._load()

    # --- internals ----------------------------------------------------------
    def _load(self) -> bool:
        with self._lock:
            if self._session is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True
            try:
                # Imported here, not at module top, so a hub without onnxruntime
                # still starts — the same rule vlm.py and mqtt_bus.py follow.
                from . import pose_pipeline

                t0 = time.monotonic()
                self._session, self._input_name = pose_pipeline.build_session(quiet=True)
                self._meta = pose_pipeline.load_metadata()

                from ..qnn_session import resolved_mode
                self._mode = resolved_mode(self._session)

                log.info("Pose model loaded in %.2fs (mode=%s)",
                         time.monotonic() - t0, self._mode)
                if self._mode == "cpu":
                    log.warning("Pose is running on CPU, roughly 30x slower than the NPU. "
                                "Check that onnxruntime-qnn is installed and the model exported.")
                return True
            except Exception as e:
                self._load_error = str(e)
                log.info("Pose unavailable: %s", e)
                self._session = None
                return False

    # --- inference ----------------------------------------------------------
    def estimate(self, image_path: str, person_box=None) -> dict:
        """Estimate one person's pose. Never raises.

        `person_box` is the unpadded person rect inside the crop, as the edge
        measured it. The crop itself is framed for face detection, so the person
        fills only about half of it; without this the subject lands at roughly
        half the model's usable input height and keypoint accuracy suffers for
        no reason. Absent, the whole crop is used.
        """
        if not self.is_available():
            return self._unavailable(self._load_error or "pose model not loaded")

        try:
            import cv2

            from . import pose_pipeline

            image = cv2.imread(image_path)
            if image is None:
                return self._unavailable(f"could not read {image_path}")

            t0 = time.monotonic()
            tensor, transform = pose_pipeline.preprocess(image, person_box, self._meta)
            with self._lock:
                outputs = self._session.run(None, {self._input_name: tensor})
            keypoints = pose_pipeline.decode_heatmap(outputs[0], transform, self._meta)
            latency_s = time.monotonic() - t0

            score = pose_pipeline.mean_score(keypoints)
            if score < self.min_mean_score:
                return {
                    "available": True, "status": "no_pose",
                    "keypoints": None, "mean_score": score,
                    "latency_s": latency_s, "error": None,
                }

            return {
                "available": True, "status": "ok",
                "keypoints": keypoints, "mean_score": score,
                "latency_s": latency_s, "error": None,
            }
        except Exception as e:
            # A malformed crop must not take down the request that carried it —
            # face ID may still have succeeded on the same upload.
            log.warning("Pose estimation failed: %s", e)
            return self._unavailable(str(e))

    @staticmethod
    def _unavailable(error: str) -> dict:
        return {
            "available": False, "status": "unavailable",
            "keypoints": None, "mean_score": None,
            "latency_s": None, "error": error,
        }
