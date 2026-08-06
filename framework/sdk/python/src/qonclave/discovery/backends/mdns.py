"""
mdns.py -- mDNS / DNS-SD discovery.

The open standard COMMUNICATION.md section 1 settles on, so an ESP32 or a Mac can discover nodes
with built-in OS libraries rather than a bespoke socket listener.
"""

from __future__ import annotations
