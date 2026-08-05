"""
events.py -- ring buffers for the dashboard.

Ephemeral operational state only. Historical state belongs to storage/, which is what frees a hub
from maintaining a database (ARCHITECTURE.md section 1).

Origin: hub/framework/events.py and hub/framework/recognize_activity.py
"""

from __future__ import annotations
