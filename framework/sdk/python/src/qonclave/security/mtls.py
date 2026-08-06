"""
mtls.py -- SSL contexts for IP transports.

Required by the `full` profile. Deliberately NOT required by `constrained` or `minimal`: a full
handshake on an ESP32 costs seconds and hundreds of KB of RAM, which is more energy than the
message it protects. Those profiles use security/psk.py instead.

Spec: spec/v1/profiles/, SECURITY.md section 3
"""

from __future__ import annotations
