"""
actuators.py -- the actuator sink contract.

Applies commands received inline or drained from the hub mailbox. Implementations MUST check
`expires_at` before acting: a device that wakes to a day-old unlock command and executes it is a
security failure, not a late delivery.
"""

from __future__ import annotations
