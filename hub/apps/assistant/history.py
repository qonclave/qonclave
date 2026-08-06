"""
In-memory conversation history per device_id.
Max 10 turns (5 user+assistant exchanges) — oldest pair dropped when full.
"""
from __future__ import annotations

import threading
from collections import deque

_lock = threading.Lock()
_histories: dict[str, deque] = {}

MAX_TURNS = 10  # total turns (user + assistant), so 5 exchanges


def get_history(device_id: str) -> list[dict]:
    with _lock:
        return list(_histories.get(device_id, deque()))


def append_turn(device_id: str, role: str, content: str) -> None:
    with _lock:
        if device_id not in _histories:
            _histories[device_id] = deque(maxlen=MAX_TURNS)
        _histories[device_id].append({"role": role, "content": content})


def clear_history(device_id: str) -> None:
    with _lock:
        _histories.pop(device_id, None)
