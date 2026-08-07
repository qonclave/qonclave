"""
icons.py — Level 2 Central Hub cache for the edge's 12x8 LED matrix icons.

Icons are rendered by a deterministic local generator; the VLM is NOT involved.

It used to be: each cache miss ran a VLM query asking for a JSON bitmap, and a
boot-time thread pre-warmed every label the cache had ever seen. Both were
removed because they cost the one thing this hub cannot spare:

  * The VLM is single-instance and serialized (vlm.py takes a lock around
    generation), and it is shared with posture investigations. Icon synthesis
    therefore delayed emergency reasoning -- boot warming worst of all, firing
    dozens of queries in the minutes right after a restart.
  * It never worked anyway. The icon prompt failed in prompt processing
    (GenieXError -201201, empty result) and fell through to the deterministic
    generator below, so the bitmaps on the LED matrix were already these.

Paying an emergency-reasoning delay for output that was being thrown away is
the trade this module no longer makes. The 30-minute TTL is kept so cached
entries still age out and refresh, which is now instant and offline.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger("qonclave.icons")

TTL_SECONDS = 1800.0  # 30 minutes
HUB_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "icons_cache.json")

_cache_lock = threading.Lock()
_hub_icon_cache: dict[str, dict[str, Any]] = {}


def _render_10x6(label: str) -> list[list[int]]:
    """Deterministic 10x6 silhouette for a label. Instant, offline, stable:
    the same label always renders the same bitmap, so a TTL refresh is a
    no-op rather than a fresh guess.

    Each row's left half is 5 bits taken from a rolling hash of the label and
    mirrored, so distinct labels get distinct shapes. (The previous version
    branched on ``hash % 2`` and so had only TWO possible outputs for every
    label in existence -- a 'dog' and a 'skateboard' lit identical pixels.)
    """
    h = 0
    for i, ch in enumerate(label):
        h = (h * 131 + ord(ch) * (i + 1)) & 0xFFFFFFFF

    grid = [[0] * 10 for _ in range(6)]
    for r in range(6):
        row_bits = (h >> (r * 5)) & 0x1F  # 5 bits -> the left half of this row
        for c in range(5):
            if (row_bits >> (4 - c)) & 1:
                grid[r][c] = 1
                grid[r][9 - c] = 1  # mirror: silhouettes read as symmetrical
    # A filled core so every icon reads as one solid object on an 8x12 matrix
    # rather than scattered pixels, whatever the hash produced around it.
    for r in (2, 3):
        grid[r][4] = grid[r][5] = 1
    return grid


def _wrap_10x6_to_12x8(grid_10x6: list[list[int]]) -> list[list[int]]:
    """Wraps a 10x6 grid with a 1-pixel empty outer border of OFF LEDs (0) to produce 12x8."""
    grid_12x8: list[list[int]] = []
    grid_12x8.append([0] * 12)  # top border
    for row in grid_10x6[:6]:
        r = [0] + (row + [0] * 10)[:10] + [0]
        grid_12x8.append(r)
    while len(grid_12x8) < 7:
        grid_12x8.append([0] * 12)
    grid_12x8.append([0] * 12)  # bottom border
    return grid_12x8[:8]


def render_icon(label: str) -> list[list[int]]:
    """The 12x8 bitmap for a label."""
    return _wrap_10x6_to_12x8(_render_10x6(label))


def load_cache() -> None:
    """Loads Level 2 Hub cache from disk, converting legacy formats to TTL dictionaries."""
    global _hub_icon_cache
    with _cache_lock:
        if os.path.exists(HUB_CACHE_FILE):
            try:
                with open(HUB_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, val in data.items():
                        if isinstance(val, list):
                            # Legacy format: treat as expired so it gets refreshed organically
                            _hub_icon_cache[k] = {
                                "bitmap": val,
                                "updated_at": 0.0,
                                "last_requested_at": 0.0,
                                "permanent": k in ("clear", "green")
                            }
                        elif isinstance(val, dict):
                            _hub_icon_cache[k] = val
                log.info("Loaded Level 2 Hub icon cache with %d entries from %s", len(_hub_icon_cache), HUB_CACHE_FILE)
            except Exception as e:
                log.error("Failed to read %s: %s", HUB_CACHE_FILE, e)

        # Ensure permanent control states exist
        now = time.time()
        if "clear" not in _hub_icon_cache:
            _hub_icon_cache["clear"] = {"bitmap": [[0]*12 for _ in range(8)], "updated_at": now, "last_requested_at": now, "permanent": True}
        if "green" not in _hub_icon_cache:
            _hub_icon_cache["green"] = {"bitmap": [[1]*12 for _ in range(8)], "updated_at": now, "last_requested_at": now, "permanent": True}


def save_cache() -> None:
    """Persists Level 2 Hub cache to disk."""
    with _cache_lock:
        try:
            with open(HUB_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(_hub_icon_cache, f, indent=2)
        except Exception as e:
            log.error("Failed to save %s: %s", HUB_CACHE_FILE, e)


def get_or_generate_icon(label: str) -> dict[str, Any]:
    """Retrieves an icon from the Level 2 cache, rendering it if expired/missing."""
    label = label.lower().strip()
    if not label:
        label = "clear"

    now = time.time()
    with _cache_lock:
        entry = _hub_icon_cache.get(label)
        if entry:
            entry["last_requested_at"] = now
            if entry.get("permanent", False):
                return entry
            age = now - entry.get("updated_at", 0.0)
            if age <= TTL_SECONDS and entry.get("bitmap"):
                log.debug("Level 2 Hub cache hit for '%s' (age: %.1f s)", label, age)
                return entry

    log.info("Level 2 Hub cache miss/expired for '%s'; rendering locally", label)
    bitmap = render_icon(label)

    with _cache_lock:
        _hub_icon_cache[label] = {
            "bitmap": bitmap,
            "updated_at": time.time(),
            "last_requested_at": time.time(),
            "permanent": False
        }
    save_cache()
    return _hub_icon_cache[label]


# Boot-time cache warming is gone with the VLM path. Pre-rendering labels
# nobody has asked for yet buys nothing now that a miss is a microsecond of
# local arithmetic, and as a VLM loop it was the worst offender: dozens of
# queries in the minutes right after a restart, each one able to delay a
# posture investigation waiting on the same serialized model.
