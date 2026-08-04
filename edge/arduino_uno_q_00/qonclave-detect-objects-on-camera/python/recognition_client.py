# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
recognition_client.py -- sends sampled person crops to the hub's
POST /recognize endpoint and reports back the parsed identity result.

Sampling policy (kept here, not in main.py, so it's independently testable):
  - immediately, the first time a track_id is seen
  - thereafter, at most once every `sample_interval_sec` while the track's
    identity is not yet known (see IdentityMap's sticky-known rule)
  - never again once the track is known
At most one request is ever in flight per track_id, so a slow hub response
can't pile up requests -- this plus the interval is the whole rate limit.
No frame is ever streamed continuously; only sampled crops are sent.
"""

from __future__ import annotations

import threading
import time

import requests


class RecognitionClient:
    def __init__(self, get_hub_base_url, timeout_sec: float = 5.0,
                 sample_interval_sec: float = 1.0, logger=None):
        self._get_hub_base_url = get_hub_base_url
        self.timeout_sec = timeout_sec
        self.sample_interval_sec = sample_interval_sec
        self._log = logger

        self._lock = threading.Lock()
        self._last_sent_at: dict[int, float] = {}
        self._in_flight: set[int] = set()

    def should_sample(self, track_id: int, is_known: bool, now: "float | None" = None) -> bool:
        """Whether now is the moment to send another sample for track_id."""
        if is_known:
            return False
        now = time.monotonic() if now is None else now
        with self._lock:
            if track_id in self._in_flight:
                return False
            last = self._last_sent_at.get(track_id)
            if last is None:
                return True  # first time this track has ever been seen
            return (now - last) >= self.sample_interval_sec

    def claim(self, track_id: int, now: "float | None" = None) -> None:
        """Reserve track_id for sampling right now. Cheap and synchronous by
        design -- call this from the hot detection-callback path immediately
        after should_sample() returns True, *before* doing any crop/encode
        work, so a second frame arriving while that work is still in
        progress sees the track as already claimed instead of also firing.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            self._in_flight.add(track_id)
            self._last_sent_at[track_id] = now

    def release(self, track_id: int) -> None:
        """Undo a claim without sending (e.g. the crop was rejected as too
        small/clipped), so the track is retried on the next interval instead
        of looking permanently in-flight."""
        with self._lock:
            self._in_flight.discard(track_id)

    def send_claimed(self, track_id: int, crop_jpeg: bytes, on_result) -> None:
        """POST /recognize for a track already claim()'d. Performs the HTTP
        call inline in the calling thread -- call this from a background
        thread you've already started (after doing crop/encode work there
        too), not from the hot detection-callback path.

        on_result(track_id, result_dict, latency_s) is invoked exactly once,
        with the parsed hub response on success or a synthetic
        {"identity": "error", "status": "error", "confidence": 0.0} on
        failure/timeout -- callers don't need a separate error path.
        """
        self._send(track_id, crop_jpeg, on_result)

    def _send(self, track_id, crop_jpeg, on_result):
        t0 = time.monotonic()
        try:
            url = f"{self._get_hub_base_url()}/recognize"
            resp = requests.post(
                url,
                data={"track_id": str(track_id)},
                files={"image": (f"track_{track_id}.jpg", crop_jpeg, "image/jpeg")},
                timeout=self.timeout_sec,
            )
            latency_s = time.monotonic() - t0
            resp.raise_for_status()
            result = resp.json()
            if self._log:
                self._log.info(
                    f"Recognize track {track_id}: {result.get('status')} "
                    f"({result.get('identity')}, {result.get('confidence', 0.0):.2f}) "
                    f"in {latency_s * 1000:.0f}ms"
                )
        except Exception as e:
            latency_s = time.monotonic() - t0
            result = {"track_id": track_id, "identity": "error", "confidence": 0.0, "status": "error"}
            if self._log:
                self._log.warning(f"Recognize track {track_id} failed after {latency_s * 1000:.0f}ms: {e}")
        finally:
            with self._lock:
                self._in_flight.discard(track_id)

        try:
            on_result(track_id, result, latency_s)
        except Exception as e:
            if self._log:
                self._log.error(f"Recognize on_result callback failed for track {track_id}: {e}")

    def forget(self, track_id: int) -> None:
        """Drop sampling state for a track that's no longer active."""
        with self._lock:
            self._last_sent_at.pop(track_id, None)
            self._in_flight.discard(track_id)
