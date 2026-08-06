"""
ids.py — identifier generation.

Ids are prefixed by kind ("evt-", "task-", "cmd-", "grant-") so a value appearing in a log or an
archived record is self-describing. Length is bounded to keep a constrained device's buffers
fixed-size.

Spec: spec/v1/json-schema/common.schema.json#/$defs/nodeId
"""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def new_id(prefix: str, *, entropy_bytes: int = 6) -> str:
    """A sortable, prefixed identifier.

    Time component first so ids sort chronologically, which makes a ring buffer or an object-store
    listing readable without a separate index. Random suffix from os.urandom so two nodes
    generating ids in the same millisecond do not collide.
    """
    stamp = _b36(int(time.time() * 1000))
    rand = _b36(int.from_bytes(os.urandom(entropy_bytes), "big"))
    return f"{prefix}-{stamp}{rand}"


def event_id() -> str:
    return new_id("evt")


def task_id() -> str:
    return new_id("task")


def command_id() -> str:
    return new_id("cmd")


def grant_id() -> str:
    return new_id("grant")
