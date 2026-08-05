"""
coap.py -- CoAP transport for constrained devices.

UDP-based, so a device that cannot sustain a TCP connection can still push events. Named as a
required ingestion path in COMMUNICATION.md section 2.
"""

from __future__ import annotations
