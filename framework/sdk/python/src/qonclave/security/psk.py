"""
psk.py -- the constrained crypto path.

Derives a session key from the pre-shared key established at out-of-band commissioning, and signs
payloads with HS256. This is the floor for `constrained` and `minimal`; mTLS is permitted where
the platform can afford it.

Spec: spec/v1/profiles/constrained.md, SECURITY.md section 3
"""

from __future__ import annotations
