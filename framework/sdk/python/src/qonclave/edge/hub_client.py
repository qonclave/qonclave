"""
hub_client.py -- talking to a hub.

Handles both cases: the home hub that commissioned this device, and a foreign hub the device
holds a capability grant for. The second is what makes ARCHITECTURE.md's self-healing handoff
mechanically possible.
"""

from __future__ import annotations
