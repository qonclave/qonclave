# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
analysis_client.py -- sends sampled person crops to the hub's
POST /track/analyze and reports back the parsed per-analyzer result.

Replaces recognition_client.py. The structure is the same -- the careful
claim-before-crop-work ordering, one request in flight per track, a synthetic
error result so callers need no error path -- but the sampling policy is now
per analyzer rather than face-only:

    face   sample until status == "known", then never again  (unchanged rule)
    pose   every POSE_SAMPLE_INTERVAL_SEC while the track is alive

The old rule stopped sampling entirely once a face was known, which is right for
identity and wrong for pose: fall detection needs a continuous time series for
as long as a person is tracked. Unioning the two means one crop serves whichever
analyzers are due, so a known person costs one request per pose tick instead of
two.

`_in_flight` stays PER TRACK, not per analyzer. One request carries every due
analyzer, so a second request for the same track while one is open would send a
duplicate crop, not a useful extra sample.
"""

from __future__ import annotations

import threading
import time

import requests

FACE = "face"
POSE = "pose"


class AnalysisClient:
    def __init__(self, get_hub_base_url, timeout_sec: float = 5.0,
                 face_interval_sec: float = 1.0,
                 pose_interval_sec: float = 0.25,
                 analyzers=(FACE, POSE), logger=None):
        self._get_hub_base_url = get_hub_base_url
        self.timeout_sec = timeout_sec
        self.analyzers = tuple(analyzers)
        self._intervals = {FACE: face_interval_sec, POSE: pose_interval_sec}
        self._log = logger

        self._lock = threading.Lock()
        # track_id -> {analyzer: last_sent_monotonic}
        self._last_sent_at: dict[int, dict[str, float]] = {}
        self._in_flight: set[int] = set()

    # --- sampling policy ----------------------------------------------------
    def analyzers_due(self, track_id: int, is_known: bool,
                      now: "float | None" = None) -> set:
        """Which analyzers want a sample of this track right now.

        Empty means skip the track entirely — no crop, no request. That is the
        common case at 4 Hz once a face is known, and it is why this returns a
        set rather than a bool.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            if track_id in self._in_flight:
                return set()
            stamps = self._last_sent_at.get(track_id, {})
            due = set()
            for analyzer in self.analyzers:
                # The sticky-known rule: identity does not change, so once it is
                # resolved there is nothing left to ask.
                if analyzer == FACE and is_known:
                    continue
                last = stamps.get(analyzer)
                if last is None or (now - last) >= self._intervals.get(analyzer, 1.0):
                    due.add(analyzer)
            return due

    def claim(self, track_id: int, analyzers, now: "float | None" = None) -> None:
        """Reserve this track and stamp the analyzers being sent.

        Cheap and synchronous by design: call it from the hot detection-callback
        path immediately after analyzers_due(), BEFORE any crop/encode work, so
        a second frame arriving while that work is still in progress sees the
        track as claimed instead of also firing.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            self._in_flight.add(track_id)
            stamps = self._last_sent_at.setdefault(track_id, {})
            for analyzer in analyzers:
                stamps[analyzer] = now

    def release(self, track_id: int) -> None:
        """Undo a claim without sending — the crop was rejected, say — so the
        track is retried next interval instead of looking permanently in
        flight."""
        with self._lock:
            self._in_flight.discard(track_id)

    def forget(self, track_id: int) -> None:
        with self._lock:
            self._last_sent_at.pop(track_id, None)
            self._in_flight.discard(track_id)

    # --- sending ------------------------------------------------------------
    def send_claimed(self, track_id: int, crop_jpeg: bytes, analyzers,
                     on_result, person_box=None) -> None:
        """POST /track/analyze for a track already claim()'d.

        Performs the HTTP call inline, so call it from a background thread you
        have already started — not from the detection callback.

        on_result(track_id, result, latency_s) runs exactly once, with the
        parsed response or a synthetic error result of the same shape.
        """
        t0 = time.monotonic()
        try:
            data = {"track_id": str(track_id), "analyzers": ",".join(sorted(analyzers))}
            if person_box is not None:
                data["person_box"] = ",".join(str(int(v)) for v in person_box)

            resp = requests.post(
                f"{self._get_hub_base_url()}/track/analyze",
                data=data,
                files={"image": (f"track_{track_id}.jpg", crop_jpeg, "image/jpeg")},
                timeout=self.timeout_sec,
            )
            latency_s = time.monotonic() - t0
            resp.raise_for_status()
            result = resp.json()
            self._log_result(track_id, result, latency_s)
        except Exception as e:
            latency_s = time.monotonic() - t0
            result = self._error_result(track_id)
            if self._log:
                self._log.warning(
                    f"Analyze track {track_id} failed after {latency_s * 1000:.0f}ms: {e}")
        finally:
            with self._lock:
                self._in_flight.discard(track_id)

        try:
            on_result(track_id, result, latency_s)
        except Exception as e:
            if self._log:
                self._log.error(f"Analyze on_result callback failed for track {track_id}: {e}")

    @staticmethod
    def _error_result(track_id: int) -> dict:
        """Same shape as a real response, so callers need no error branch.

        `face.status == "error"` is distinct from `"unavailable"`: unavailable
        means the hub answered and said it cannot do this, error means we never
        heard back. IdentityMap treats both as non-identifying, but a human
        reading the logs needs to tell a missing model from a missing network.
        """
        return {
            "track_id": track_id,
            "face": {"identity": "error", "confidence": 0.0, "status": "error"},
            "pose": {"status": "error", "keypoints": None, "mean_score": None},
            "latency_ms": {},
        }

    def _log_result(self, track_id: int, result: dict, latency_s: float) -> None:
        if not self._log:
            return
        face = result.get("face") or {}
        pose = result.get("pose") or {}
        parts = []
        if face:
            parts.append(f"face={face.get('status')}"
                         + (f" ({face.get('identity')})" if face.get("status") == "known" else ""))
        if pose:
            score = pose.get("mean_score")
            parts.append(f"pose={pose.get('status')}"
                         + (f" ({score:.2f})" if isinstance(score, (int, float)) else ""))
        self._log.info(f"Analyze track {track_id}: {', '.join(parts)} "
                       f"in {latency_s * 1000:.0f}ms")
