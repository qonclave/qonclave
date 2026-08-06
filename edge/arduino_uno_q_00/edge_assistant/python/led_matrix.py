"""UNO Q LED matrix state indicators.

State changes enqueue non-blocking LED updates. A background worker sends assistant
state names to the sketch over Arduino Bridge, so audio capture/STT never waits on
LED I/O. The sketch owns the physical Arduino_LED_Matrix rendering and local
thinking animation.
"""
from __future__ import annotations

import logging
import threading

from arduino.app_utils import Bridge

log = logging.getLogger(__name__)

_FALLBACK_ICON_ROWS: dict[str, tuple[str, ...]] = {
    "idle": (
        "000000000000",
        "000000000000",
        "000000000000",
        "000000000000",
        "000000000000",
        "000000000000",
        "000000000000",
        "000000000000",
    ),
    "listening": (
        "000001100000",
        "000011110000",
        "000011110000",
        "000011110000",
        "000001100000",
        "000111111000",
        "000001100000",
        "000111111000",
    ),
    "thinking": (
        "000000000000",
        "000000000000",
        "000100010001",
        "000000000000",
        "000100010001",
        "000000000000",
        "000000000000",
        "000000000000",
    ),
    "speaking": (
        "001100000100",
        "001100001010",
        "001101010010",
        "001101010010",
        "001101010010",
        "001100001010",
        "001100000100",
        "000000000000",
    ),
}

_condition = threading.Condition()
_last_requested_state = ""
_pending_state: str | None = None
_worker_started = False


def _fallback_bitstring_for_state(state: str) -> str:
    rows = _FALLBACK_ICON_ROWS.get(state, _FALLBACK_ICON_ROWS["idle"])
    return "".join(rows)


def _send_state_to_bridge(state: str) -> None:
    try:
        Bridge.call("set_led_state", state)
    except Exception as exc:
        log.debug("LED state update failed for %r; trying bitmap fallback: %s", state, exc)
        try:
            Bridge.call("set_custom_led_array", _fallback_bitstring_for_state(state))
        except Exception as fallback_exc:
            log.debug("LED bitmap fallback failed for state %r: %s", state, fallback_exc)


def _worker() -> None:
    global _pending_state
    while True:
        with _condition:
            while _pending_state is None:
                _condition.wait()
            state = _pending_state
            _pending_state = None
        _send_state_to_bridge(state)


def _ensure_worker_started() -> None:
    global _worker_started
    if _worker_started:
        return
    with _condition:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker, daemon=True, name="led-matrix")
        thread.start()
        _worker_started = True


def set_state(state: str) -> None:
    """Mirror the assistant state on the UNO Q 12x8 LED matrix without blocking audio."""
    global _last_requested_state, _pending_state
    if state == _last_requested_state:
        return
    _last_requested_state = state
    _ensure_worker_started()
    with _condition:
        _pending_state = state
        _condition.notify()
