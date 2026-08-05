"""
commissioning.py -- out-of-band pairing.

An operator scans a QR code on the device, transferring its address and pre-shared key to the hub
without broadcasting secrets over the air.

This is also where a `minimal` device learns its hub endpoint, which it then keeps in flash and
uses forever -- discovery is forbidden on that profile because an mDNS browse costs more radio
time than the device's entire useful exchange.

Spec: spec/v1/profiles/minimal.md, SECURITY.md section 3
"""

from __future__ import annotations
