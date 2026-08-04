"""
Simple tool dispatch for the assistant.
Currently supports a weather stub; extend by adding entries to TOOLS.
"""
from __future__ import annotations

import os

WEATHER_KEYWORDS = {"weather", "temperature", "forecast", "rain", "sunny", "cold", "hot", "warm"}

_LOCATION = os.environ.get("ASSISTANT_WEATHER_LOCATION", "your city")


def maybe_call_tool(text: str) -> tuple[str | None, str | None]:
    """Return (tool_name, result_string) if a tool matches, else (None, None)."""
    lower = text.lower()
    if any(kw in lower for kw in WEATHER_KEYWORDS):
        location = os.environ.get("ASSISTANT_WEATHER_LOCATION", _LOCATION)
        result = f"The current weather in {location} is sunny with 22°C and a light breeze."
        return "weather", result
    return None, None
