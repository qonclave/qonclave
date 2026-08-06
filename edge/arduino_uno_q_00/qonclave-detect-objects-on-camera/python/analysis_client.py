# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
analysis_client.py -- sends sampled person crops to the hub's
POST /track/analyze endpoint and reports back the parsed per-analyzer result.
Replaces recognition_client.py: same claim/release/forget structure, but the
sampling policy is per-analyzer, unioned into one request.

Sampling policy (kept here, not in main.py, so it's independently testable):
  - face: immediately the first time a track_id is seen, then at most once
    every `face_interval_sec` while the track's identity is not yet known
    (IdentityMap's upgrade-only rule) -- never again once known. Unchanged
    from the old RecognitionClient.
  - pose: every `pose_interval_sec` (~4 Hz) for as long as the track is
    alive, known or not -- fall detection needs a continuous time series.
One crop serves whichever analyzers are due, so a known person costs one
request per pose tick instead of two. At most one request is ever in flight
per track_id (not per analyzer), so a slow hub response can't pile up
requests -- this plus the intervals is the whole rate limit. No frame is ever
streamed continuously; only sampled crops are sent.
"""

from __future__ import annotations

import threading
import time

import requests

ANALYZERS = ("face", "pose")


class AnalysisClient:
    def __init__(self, get_hub_base_url, timeout_sec: float = 5.0,
                 face_interval_sec: float = 0.5, pose_interval_sec: float = 0.25,
                 analyzers=ANALYZERS, logger=None, device_id=None):
        self._get_hub_base_url = get_hub_base_url
        # Sent with every request so the hub knows which device to push MQTT
        # commands (e.g. capture_investigation_image) back to, now that the
        # periodic /edge/event escalation no longer announces it.
        self.device_id = device_id
        self.timeout_sec = timeout_sec
        self.intervals = {"face": face_interval_sec, "pose": pose_interval_sec}
        self.analyzers = tuple(a for a in ANALYZERS if a in analyzers)
        self._log = logger

        self._lock = threading.Lock()
        # per-track, per-analyzer monotonic timestamp of the last claim
        self._last_sent_at: dict[int, dict[str, float]] = {}
        # per-TRACK (one request carries all due analyzers)
        self._in_flight: set[int] = set()

    def analyzers_due(self, track_id: int, is_known: bool,
                      now: "float | None" = None) -> set:
        """Which analyzers want a sample of track_id right now. Empty set
        while a request is in flight for the track."""
        now = time.monotonic() if now is None else now
        due = set()
        with self._lock:
            if track_id in self._in_flight:
                return due
            stamps = self._last_sent_at.get(track_id, {})
            for analyzer in self.analyzers:
                if analyzer == "face" and is_known:
                    continue  # identity resolved; never sample the face again
                last = stamps.get(analyzer)
                if last is None or (now - last) >= self.intervals[analyzer]:
                    due.add(analyzer)
        return due

    def claim(self, track_id: int, analyzers, now: "float | None" = None) -> None:
        """Reserve track_id for sampling right now, stamping each claimed
        analyzer. Cheap and synchronous by design -- call this from the hot
        detection-callback path immediately after analyzers_due() returns a
        non-empty set, *before* doing any crop/encode work, so a second frame
        arriving while that work is still in progress sees the track as
        already claimed instead of also firing."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._in_flight.add(track_id)
            stamps = self._last_sent_at.setdefault(track_id, {})
            for analyzer in analyzers:
                stamps[analyzer] = now

    def release(self, track_id: int) -> None:
        """Undo a claim without sending (e.g. the crop was rejected as too
        small/clipped), so the track is retried on the next interval instead
        of looking permanently in-flight. Deliberately does NOT clear the
        analyzer stamps: a rejected crop still counts as "just tried"."""
        with self._lock:
            self._in_flight.discard(track_id)

    def send_claimed(self, track_id: int, crop_jpeg: bytes, analyzers,
                     on_result, person_box=None, known_identity=None) -> None:
        """POST /track/analyze for a track already claim()'d. Performs the
        HTTP call inline in the calling thread -- call this from a background
        thread you've already started (after doing crop/encode work there
        too), not from the hot detection-callback path.

        person_box: optional (x1, y1, x2, y2) of the unpadded person rect in
        the crop's own pixels, so the hub can re-frame pose tightly while
        face ID keeps the full (face-framed) crop.

        on_result(track_id, result_dict, latency_s) is invoked exactly once,
        with the parsed hub response on success or a synthetic error result
        on failure/timeout (each requested analyzer present with
        status "error") -- callers don't need a separate error path.
        """
        self._send(track_id, crop_jpeg, analyzers, on_result, person_box,
                   known_identity)

    def _send(self, track_id, crop_jpeg, analyzers, on_result, person_box,
              known_identity):
        analyzer_list = sorted(analyzers)
        t0 = time.monotonic()
        try:
            url = f"{self._get_hub_base_url()}/track/analyze"
            data = {"track_id": str(track_id), "analyzers": ",".join(analyzer_list)}
            if self.device_id:
                data["device_id"] = str(self.device_id)
            if person_box is not None:
                data["person_box"] = ",".join(str(int(v)) for v in person_box)
            if known_identity:
                data["known_identity"] = str(known_identity)
            resp = requests.post(
                url,
                data=data,
                files={"image": (f"track_{track_id}.jpg", crop_jpeg, "image/jpeg")},
                timeout=self.timeout_sec,
            )
            latency_s = time.monotonic() - t0
            resp.raise_for_status()
            result = resp.json()
            if self._log:
                face = result.get("face") or {}
                pose = result.get("pose") or {}
                parts = []
                if face:
                    parts.append(f"face={face.get('status')}"
                                 f" ({face.get('identity')}, {face.get('confidence', 0.0):.2f})")
                if pose:
                    parts.append(f"pose={pose.get('status')}")
                self._log.info(
                    f"Analyze track {track_id} [{','.join(analyzer_list)}]: "
                    f"{' '.join(parts)} in {latency_s * 1000:.0f}ms"
                )
        except Exception as e:
            latency_s = time.monotonic() - t0
            # Same shape as a real response so callers need no error path.
            result = {"track_id": track_id}
            if "face" in analyzers:
                result["face"] = {"identity": "error", "confidence": 0.0, "status": "error"}
            if "pose" in analyzers:
                result["pose"] = {"status": "error", "keypoints": None, "mean_score": None}
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

    def forget(self, track_id: int) -> None:
        """Drop sampling state for a track that's no longer active."""
        with self._lock:
            self._last_sent_at.pop(track_id, None)
            self._in_flight.discard(track_id)
