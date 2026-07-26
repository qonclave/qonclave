"""
icons.py — Level 2 Central Hub Caching, Request-Driven Tracking, & Boot-Time LLM Warming.

Implements a 30-minute (1800s) TTL age-out across all dynamically requested object icons.
Tracks object request history from edge devices and runs Boot-Time LLM Cache Warming
on historically requested objects when the server boots.
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


def _get_fallback_10x6(label: str) -> list[list[int]]:
    """Deterministic fallback generator for 10x6 grid when VLM is offline or busy."""
    h = sum(ord(c) * (i + 1) for i, c in enumerate(label))
    grid = [[0] * 10 for _ in range(6)]
    # Draw an interesting symmetrical silhouette pattern based on hash
    for r in range(1, 5):
        for c in range(2, 5):
            if (h + r * c) % 2 == 0 or r == 1 or r == 4:
                grid[r][c] = 1
                grid[r][9 - c] = 1  # symmetry
    # Center fill
    grid[2][4] = grid[2][5] = 1
    grid[3][4] = grid[3][5] = 1
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


def _ensure_blank_image() -> str:
    """Creates a temporary 1x1 image for VLM text-prompts when no camera frame is provided."""
    tmp_path = os.path.join(os.path.dirname(__file__), "..", "scratch_blank.jpg")
    if not os.path.exists(tmp_path):
        try:
            with open(tmp_path, "wb") as f:
                # 1x1 JPEG minimal bytes
                f.write(bytes.fromhex("ffd8ffe000104a46494600010101004800480000ffdb0043000302020302020303030304030304050805050404050a070706080c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113141515150c0f171816141812141514ffd9"))
        except Exception:
            pass
    return tmp_path


def _synthesize_icon(label: str, vlm: Any, image_path: str | None = None) -> list[list[int]]:
    """Uses local VLM reasoning to generate a 10x6 silhouette and wraps to 12x8."""
    img = image_path if (image_path and os.path.exists(image_path)) else _ensure_blank_image()
    prompt = (
        f"You are an expert LED matrix icon designer. Generate a centered 10x6 binary grid "
        f"silhouette representing a '{label}'. Output ONLY a valid JSON array of 6 rows, "
        f"each row containing exactly 10 integers (0 for OFF, 1 for ON). Example for 'box': "
        f"[[0,1,1,1,1,1,1,1,1,0],[0,1,0,0,0,0,0,0,1,0],[0,1,0,0,0,0,0,0,1,0],[0,1,0,0,0,0,0,0,1,0],[0,1,0,0,0,0,0,0,1,0],[0,1,1,1,1,1,1,1,1,0]]"
    )

    grid_10x6 = None
    if vlm and getattr(vlm, "is_available", lambda: False)():
        try:
            res = vlm.structured_query(img, prompt=prompt, max_new_tokens=256)
            parsed = res.get("parsed")
            if isinstance(parsed, list) and len(parsed) >= 6 and all(isinstance(r, list) and len(r) >= 10 for r in parsed[:6]):
                grid_10x6 = [[1 if val else 0 for val in row[:10]] for row in parsed[:6]]
                log.info("VLM successfully generated 10x6 silhouette for '%s'", label)
        except Exception as e:
            log.warning("VLM icon synthesis failed for '%s': %s", label, e)

    if not grid_10x6:
        log.info("Using fallback silhouette generator for '%s'", label)
        grid_10x6 = _get_fallback_10x6(label)

    return _wrap_10x6_to_12x8(grid_10x6)


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


def get_or_generate_icon(label: str, vlm: Any, image_path: str | None = None) -> dict[str, Any]:
    """Retrieves an icon from Level 2 cache or synthesizes a new one if expired/missing."""
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

    log.info("Level 2 Hub cache miss/expired for '%s'; synthesizing via VLM...", label)
    bitmap = _synthesize_icon(label, vlm, image_path)

    with _cache_lock:
        _hub_icon_cache[label] = {
            "bitmap": bitmap,
            "updated_at": time.time(),
            "last_requested_at": time.time(),
            "permanent": False
        }
    save_cache()
    return _hub_icon_cache[label]


def start_boot_warming(vlm: Any) -> None:
    """Starts background Boot-Time LLM Cache Warming for historically requested object classes."""
    def _warm_thread():
        time.sleep(3.0)  # Wait for server startup
        log.info("Starting Boot-Time LLM Cache Warming on historically requested objects...")
        load_cache()
        
        to_warm = []
        with _cache_lock:
            now = time.time()
            for label, entry in _hub_icon_cache.items():
                if not entry.get("permanent", False):
                    age = now - entry.get("updated_at", 0.0)
                    if age > TTL_SECONDS or entry.get("updated_at", 0.0) == 0.0:
                        to_warm.append(label)

        if not to_warm:
            log.info("No expired/unwarmed objects in Level 2 cache history. Boot-Time warming complete.")
            return

        log.info("Boot-Time LLM Cache Warming will refresh %d object(s): %s", len(to_warm), ", ".join(to_warm))
        for label in to_warm:
            log.info("Warming icon for '%s'...", label)
            bmp = _synthesize_icon(label, vlm)
            with _cache_lock:
                if label in _hub_icon_cache:
                    _hub_icon_cache[label]["bitmap"] = bmp
                    _hub_icon_cache[label]["updated_at"] = time.time()
            save_cache()
            time.sleep(1.0)  # Gentle pacing
        log.info("Boot-Time LLM Cache Warming successfully completed for %d object(s).", len(to_warm))

    threading.Thread(target=_warm_thread, name="HubBootWarming", daemon=True).start()
