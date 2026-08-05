"""
geniex.py -- GenieX / Qwen2.5-VL backend for Snapdragon X.

Imported lazily and only on ARM64, so a hub on any other machine still serves every route and
reports inference unavailable rather than failing to start.

Origin: hub/framework/vlm.py and hub/framework/llm.py
"""

from __future__ import annotations
