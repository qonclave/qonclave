"""
mailbox.py -- the per-device sleep queue.

VISION.md's asynchronous sleep-queue: hold commands for days and deliver them in the brief
milliseconds when a device wakes. Without this, a duty-cycled device has no command path at all,
because nothing is subscribed to its MQTT topic while it sleeps.

Two rules that are easy to get wrong:
  * expired commands are dropped, never delivered late
  * delivered commands are retained until acknowledged, so a device that dies mid-wake retries

Spec: spec/v1/json-schema/checkin.schema.json
"""

from __future__ import annotations
