"""
ingest.py -- normalize inbound events from any transport.

HTTP, MQTT, CoAP, and check-in uplinks all converge here into a validated EdgeEvent. Stamps
hub_received_at, which for a device reporting relative_time is the only authoritative time the
event has.

Origin: hub/framework/transport.py
"""

from __future__ import annotations
