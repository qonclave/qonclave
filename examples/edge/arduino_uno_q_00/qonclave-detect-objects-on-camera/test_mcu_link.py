# SPDX-License-Identifier: MPL-2.0

"""Tests for the MCU circuit breaker (mcu_link.py).

The behaviours that matter: a healthy link passes calls straight through; a
failing link logs once (not once per frame) and opens after a short streak of
failures so callers stop blocking on a 10s RPC timeout; an open breaker
touches the bridge zero times until its cooldown elapses; and a single
success closes it again.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from mcu_link import McuLink  # noqa: E402


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now


class FakeBridge:
    """`.call()` raises the scripted exception, or returns the scripted
    value, per invocation -- and records every attempt actually made."""

    def __init__(self):
        self.calls = []
        self._script = []

    def fail(self, error=RuntimeError("timed out after 10s")):
        self._script.append(("fail", error))

    def succeed(self, value=None):
        self._script.append(("ok", value))

    def call(self, method, *args):
        self.calls.append((method, args))
        kind, payload = self._script.pop(0)
        if kind == "fail":
            raise payload
        return payload


class FakeLogger:
    def __init__(self):
        self.info_calls = []
        self.error_calls = []

    def info(self, msg):
        self.info_calls.append(msg)

    def error(self, msg):
        self.error_calls.append(msg)


def link(**overrides):
    defaults = dict(failure_threshold=2, cooldown_seconds=15.0)
    defaults.update(overrides)
    bridge = FakeBridge()
    clock = FakeClock()
    logger = FakeLogger()
    return McuLink(bridge, clock=clock, logger=logger, **defaults), bridge, clock, logger


# --- the healthy path ---------------------------------------------------------

def test_successful_call_passes_through():
    mcu, bridge, clock, logger = link()
    bridge.succeed("ok-result")
    assert mcu.call("robot_motion_active") == "ok-result"
    assert bridge.calls == [("robot_motion_active", ())]
    assert mcu.is_available() is True


def test_forwards_positional_args():
    mcu, bridge, clock, logger = link()
    bridge.succeed(True)
    mcu.call("move_robot", "LEFT", 30)
    assert bridge.calls == [("move_robot", ("LEFT", 30))]


# --- failure + breaker ---------------------------------------------------------

def test_single_failure_returns_default_without_opening_breaker():
    mcu, bridge, clock, logger = link(failure_threshold=2)
    bridge.fail()
    result = mcu.call("robot_motion_active", default="fallback")
    assert result == "fallback"
    assert mcu.is_available() is True  # one failure, threshold is 2 -> still closed
    assert len(logger.error_calls) == 1  # logged immediately


def test_breaker_opens_after_threshold_and_skips_the_bridge():
    mcu, bridge, clock, logger = link(failure_threshold=2, cooldown_seconds=15.0)
    bridge.fail()
    bridge.fail()
    mcu.call("robot_motion_active")
    mcu.call("robot_motion_active")
    assert mcu.is_available() is False
    assert len(bridge.calls) == 2

    # Breaker open: further calls never touch the bridge at all.
    result = mcu.call("robot_motion_active", default="fallback")
    assert result == "fallback"
    assert len(bridge.calls) == 2  # unchanged -- no third attempt


def test_breaker_closes_after_cooldown_and_probes_again():
    mcu, bridge, clock, logger = link(failure_threshold=1, cooldown_seconds=15.0)
    bridge.fail()
    mcu.call("robot_motion_active")
    assert mcu.is_available() is False

    clock.now = 14.9
    assert mcu.is_available() is False
    clock.now = 15.0
    assert mcu.is_available() is True

    bridge.succeed(True)
    assert mcu.call("robot_motion_active") is True
    assert len(bridge.calls) == 2  # the probe actually reached the bridge


def test_a_single_success_resets_the_failure_streak():
    mcu, bridge, clock, logger = link(failure_threshold=3)
    bridge.fail()
    mcu.call("x")
    bridge.succeed("ok")
    mcu.call("x")
    assert mcu.status()["consecutive_failures"] == 0
    assert mcu.status()["last_error"] is None

    # Back to needing a fresh streak of 3 to open, not continuing the old one.
    bridge.fail()
    bridge.fail()
    mcu.call("x")
    mcu.call("x")
    assert mcu.is_available() is True


# --- logging is throttled, not per-call ---------------------------------------

def test_logging_is_throttled_to_first_and_every_tenth_failure():
    mcu, bridge, clock, logger = link(failure_threshold=1000)  # never opens
    for _ in range(12):
        bridge.fail()
        mcu.call("robot_motion_active")
    # Logged on failure #1 and #10, not #2-9, #11, #12.
    assert len(logger.error_calls) == 2
    assert "1 in a row" in logger.error_calls[0]
    assert "10 in a row" in logger.error_calls[1]


def test_opening_the_breaker_logs_once_not_per_skipped_call():
    mcu, bridge, clock, logger = link(failure_threshold=2, cooldown_seconds=15.0)
    bridge.fail()
    bridge.fail()
    mcu.call("x")
    mcu.call("x")
    open_logs = [m for m in logger.error_calls if "marked down" in m]
    assert len(open_logs) == 1

    for _ in range(5):
        mcu.call("x", default="fallback")
    # Skipped calls (breaker open) never touch the bridge or log again.
    assert len(bridge.calls) == 2
    assert len([m for m in logger.error_calls if "marked down" in m]) == 1


# --- status() -------------------------------------------------------------------

def test_status_shape():
    mcu, bridge, clock, logger = link()
    s = mcu.status()
    assert set(s) == {"available", "consecutive_failures", "last_error"}
    assert s == {"available": True, "consecutive_failures": 0, "last_error": None}


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
