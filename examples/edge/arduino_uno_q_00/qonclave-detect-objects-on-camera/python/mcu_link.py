# SPDX-License-Identifier: MPL-2.0

"""
mcu_link.py — a circuit breaker around Bridge.call() to the STM32 MCU.

Bridge.call() blocks for a fixed RPC timeout (10s) when the router<->MCU
serial link is desynced -- which happens, concretely, whenever the MCU gets
reflashed (an `arduino-app-cli app restart`) while arduino-router has a live
serial connection open: the reflash corrupts the router's read stream
mid-packet, and its MsgPack framing never resyncs on its own (see
arduino-router's own log: "invalid packet, expected array, got: int8").
Recovering needs a `systemctl restart arduino-router` PLUS restarting this
app to get a fresh connection -- until that happens, every single Bridge.call
in this app was blocking for 10s and logging its own failure, once per call
site, every frame: the main follow loop alone made that a ~10s freeze roughly
once a frame, forever, with unbounded duplicate "timed out after 10s" log
lines.

This wraps Bridge behind the same never-raises, degrade-to-unavailable
contract the rest of this codebase already uses for anything that can go
away underneath it (person_tracker's stale-track pruning, EdgeMQTTClient's
reconnect loop, the hub's PoseBackend/EdgeMQTTClient): call() always returns
promptly, failures are logged once then throttled instead of once per call,
and after a short run of failures the breaker OPENS -- further calls return
`default` immediately without touching the router at all, until
cooldown_seconds elapses and the next call is allowed through to probe
whether the link recovered.

Public API:
    mcu = McuLink(Bridge, logger=log)
    mcu.is_available()                        # cheap, no I/O
    mcu.call("move_robot", "LEFT", 30, default=SENTINEL)
    mcu.status()                               # for health/UI
"""

from __future__ import annotations

import time

# A call that fails (breaker open, or the attempt itself raised) returns
# `default`. Callers that need to tell "the MCU said no/None" apart from
# "the call never happened" should pass a private sentinel as `default`
# rather than relying on None -- see main.py's _FAILED.


class McuLink:
    def __init__(self, bridge, *, failure_threshold: int = 2,
                 cooldown_seconds: float = 15.0,
                 clock=time.monotonic, logger=None):
        self._bridge = bridge
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._log = logger
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._last_error: str | None = None

    def is_available(self, now: float | None = None) -> bool:
        """Cheap, no I/O: False while the breaker is open."""
        now = self._clock() if now is None else now
        return now >= self._open_until

    def status(self) -> dict:
        return {
            "available": self.is_available(),
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
        }

    def call(self, method: str, *args, default=None):
        """Bridge.call(method, *args), or `default` immediately (no I/O)
        while the breaker is open. Never raises."""
        now = self._clock()
        if now < self._open_until:
            return default
        try:
            result = self._bridge.call(method, *args)
        except Exception as e:
            self._on_failure(method, e, now)
            return default
        self._on_success()
        return result

    def _on_success(self):
        if self._consecutive_failures:
            self._info(f"MCU link recovered after {self._consecutive_failures} failed call(s)")
        self._consecutive_failures = 0
        self._last_error = None

    def _on_failure(self, method: str, error: Exception, now: float):
        self._consecutive_failures += 1
        self._last_error = str(error)
        # 1st failure, then every 10th: the caller learns immediately that
        # something broke, and again periodically while it stays broken --
        # not once per frame for as long as the outage lasts.
        if self._consecutive_failures == 1 or self._consecutive_failures % 10 == 0:
            self._error(f"MCU '{method}' failed "
                       f"({self._consecutive_failures} in a row): {error}")
        if (self._consecutive_failures >= self.failure_threshold
                and now >= self._open_until):
            self._open_until = now + self.cooldown_seconds
            self._error(f"MCU link marked down for {self.cooldown_seconds:.0f}s "
                       f"after {self._consecutive_failures} consecutive failures")

    # Logger has no cross-implementation guarantee beyond info() (see
    # mqtt_client.py) -- fall back to it rather than assume warning()/error().
    def _info(self, msg):
        if self._log:
            self._log.info(msg)

    def _error(self, msg):
        if self._log:
            (getattr(self._log, "error", None) or self._log.info)(msg)
